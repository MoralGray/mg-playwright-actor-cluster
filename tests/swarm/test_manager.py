from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from db.sqlite import init_db, open_connection
from swarm.actor import Actor
from swarm.manager import SwarmManager
from swarm.profile import load_actor
from swarm.proxy import Proxy, ProxyUnavailable, load_proxies
from swarm.state import ActorState

ACTORS_DIR = Path(__file__).resolve().parent.parent.parent / "configs" / "actors"
PROXIES_FILE = Path(__file__).resolve().parent.parent.parent / "configs" / "proxies.json"


def _run(coro):
    return asyncio.run(coro)


class TestSwarmManager(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "test.db"
        _run(init_db(self._db_path))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _manager(self) -> tuple[SwarmManager, object]:
        db = _run(open_connection(self._db_path))

        async def make() -> SwarmManager:
            return SwarmManager(db=db)

        manager = _run(make())
        return manager, db

    def _close(self, db) -> None:
        _run(db.close())

    def test_spawn_registers_and_runs_task(self) -> None:
        manager, db = self._manager()
        try:
            actor = _run(manager.spawn_actor("wildberries_user_llm"))
            self.assertIsInstance(actor, Actor)
            self.assertTrue(manager.knows("wildberries_user_llm"))
            self.assertIsNotNone(actor._task)

            async def wait() -> None:
                try:
                    await asyncio.shield(actor._task)
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass

            _run(wait())
            self.assertTrue(actor._task.done())
        finally:
            self._close(db)

    def test_stop_actor_terminates(self) -> None:
        manager, db = self._manager()
        try:
            actor = _run(manager.spawn_actor("wildberries_user_llm"))
            _run(manager.stop_actor("wildberries_user_llm"))
            self.assertTrue(actor._task.done() or actor._task.cancelled())
        finally:
            self._close(db)

    def test_pause_resume_flag(self) -> None:
        manager, db = self._manager()
        try:
            _run(manager.spawn_actor("wildberries_user_llm"))
            _run(manager.pause_actor("wildberries_user_llm"))
            self.assertTrue(manager.get("wildberries_user_llm").is_paused())
            _run(manager.resume_actor("wildberries_user_llm"))
            self.assertFalse(manager.get("wildberries_user_llm").is_paused())
            _run(manager.stop_actor("wildberries_user_llm"))
        finally:
            self._close(db)

    def test_restart_stops_then_respawns(self) -> None:
        manager, db = self._manager()
        try:
            first = _run(manager.spawn_actor("wildberries_user_llm"))
            first_task = first._task
            second = _run(manager.restart_actor("wildberries_user_llm"))
            self.assertIsNot(second, first)
            self.assertTrue(first_task.done())
            self.assertIsNotNone(second._task)
            _run(manager.stop_actor("wildberries_user_llm"))
        finally:
            self._close(db)

    def test_list_status_reports_alive_and_paused(self) -> None:
        manager, db = self._manager()
        try:
            _run(manager.spawn_actor("wildberries_user_llm"))
            statuses = manager.list_status()
            self.assertEqual(len(statuses), 1)
            self.assertEqual(statuses[0].name, "wildberries_user_llm")
            self.assertEqual(statuses[0].state, ActorState.IDLE.value)
            _run(manager.pause_actor("wildberries_user_llm"))
            statuses = manager.list_status()
            self.assertTrue(statuses[0].paused)
            _run(manager.stop_actor("wildberries_user_llm"))
        finally:
            self._close(db)

    def test_stop_all_cleans_registry(self) -> None:
        manager, db = self._manager()
        try:
            _run(manager.spawn_actor("wildberries_user_llm"))
            _run(manager.spawn_actor("wildberries_user_deterministic"))
            _run(manager.stop_all())
            for name in ("wildberries_user_llm", "wildberries_user_deterministic"):
                actor = manager.get(name)
                self.assertIsNotNone(actor)
                self.assertTrue(actor._task.done())
        finally:
            self._close(db)

    def test_session_limiter_blocks_second_concurrent_same_key(self) -> None:
        manager, db = self._manager()
        try:

            async def scenario() -> None:
                sem1 = await manager.acquire_slot("site", "ru-1")
                second_done = asyncio.Event()

                async def second_acquire() -> None:
                    await manager.acquire_slot("site", "ru-1")
                    second_done.set()

                task = asyncio.create_task(second_acquire())
                await asyncio.sleep(0.05)
                self.assertFalse(second_done.is_set(), "second acquire should block")
                manager.release_slot(sem1)
                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except TimeoutError:
                    self.fail("second acquire did not complete after release")
                self.assertTrue(second_done.is_set())

            _run(scenario())
        finally:
            self._close(db)

    def test_session_limiter_different_keys_independent(self) -> None:
        manager, db = self._manager()
        try:

            async def scenario() -> None:
                s1 = await manager.acquire_slot("siteA", "ru-1")
                s2 = await manager.acquire_slot("siteB", "ru-1")
                self.assertIsNot(s1, s2)
                manager.release_slot(s1)
                manager.release_slot(s2)

            _run(scenario())
        finally:
            self._close(db)

    def test_rotate_proxy_marks_banned_and_reassigns(self) -> None:
        proxies = load_proxies(PROXIES_FILE)
        manager = SwarmManager(
            proxies=[
                Proxy(ref=p.ref, endpoint=p.endpoint, provider=p.provider, banned=p.banned)
                for p in proxies
            ]
        )
        restarted: list[str] = []
        manager.restart_actor = lambda name: restarted.append(name) or asyncio.sleep(0)  # type: ignore[assignment]

        async def scenario() -> None:
            profile = load_actor("wildberries_user_llm", ACTORS_DIR)
            actor = manager.register(profile)
            assigned = await manager.assign_proxy(profile)
            self.assertFalse(assigned.banned)
            self.assertTrue(assigned.endpoint)
            self.assertEqual(actor.proxy_endpoint, assigned.endpoint)
            new = await manager.rotate_proxy("wildberries_user_llm")
            self.assertFalse(new.banned)
            self.assertNotEqual(new.ref, assigned.ref)
            self.assertTrue(manager.proxy_pool.get(assigned.ref).banned)
            self.assertFalse(manager.proxy_pool.get(new.ref).banned)
            self.assertEqual(manager.current_proxy("wildberries_user_llm").ref, new.ref)
            self.assertEqual(actor.proxy_endpoint, new.endpoint)
            self.assertEqual(restarted, ["wildberries_user_llm"])

        _run(scenario())

    def test_rotate_proxy_exhaustion_raises(self) -> None:
        proxies = [
            Proxy(ref="proxy-ru-1", endpoint="socks5://147.45.231.206:1080", provider="free-ru"),
            Proxy(ref="proxy-ru-2", endpoint="socks5://193.233.139.106:1080", provider="free-ru"),
        ]
        manager = SwarmManager(proxies=proxies)
        manager.restart_actor = lambda name: asyncio.sleep(0)  # type: ignore[assignment]

        async def scenario() -> None:
            profile = load_actor("wildberries_user_llm", ACTORS_DIR)
            manager.register(profile)
            await manager.assign_proxy(profile)
            with self.assertRaises(ProxyUnavailable):
                # ban both -> pool exhausted on second rotate
                await manager.rotate_proxy("wildberries_user_llm")
                await manager.rotate_proxy("wildberries_user_llm")

        _run(scenario())


if __name__ == "__main__":
    unittest.main()
