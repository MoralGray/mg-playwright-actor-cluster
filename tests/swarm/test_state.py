from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from db.sqlite import init_db, open_connection
from swarm.actor import Actor
from swarm.profile import ActorProfile, load_actor
from swarm.state import (
    TRANSITIONS,
    ActorState,
    can_transition,
    next_state,
)

ACTORS_DIR = Path(__file__).resolve().parent.parent.parent / "configs" / "actors"


class TestStateMachine(unittest.TestCase):
    def test_transitions_cover_all_states(self) -> None:
        for state in ActorState:
            self.assertIn(state, TRANSITIONS, f"missing transition for {state}")

    def test_next_state_cycle_returns_to_idle(self) -> None:
        state = ActorState.IDLE
        for _ in range(len(ActorState)):
            state = next_state(state)
        self.assertEqual(state, ActorState.IDLE)

    def test_next_state_sequence(self) -> None:
        self.assertEqual(next_state(ActorState.IDLE), ActorState.LOGIN)
        self.assertEqual(next_state(ActorState.LOGIN), ActorState.NAVIGATE)
        self.assertEqual(next_state(ActorState.NAVIGATE), ActorState.ACTION)
        self.assertEqual(next_state(ActorState.ACTION), ActorState.EXTRACT)
        self.assertEqual(next_state(ActorState.EXTRACT), ActorState.REPORT)
        self.assertEqual(next_state(ActorState.REPORT), ActorState.IDLE)

    def test_can_transition_allows_only_mapped(self) -> None:
        self.assertTrue(can_transition(ActorState.IDLE, ActorState.LOGIN))
        self.assertFalse(can_transition(ActorState.IDLE, ActorState.ACTION))
        self.assertFalse(can_transition(ActorState.LOGIN, ActorState.IDLE))


class TestProfileLoading(unittest.TestCase):
    def test_load_wildberries_user_deterministic_profile(self) -> None:
        profile = load_actor("wildberries_user_deterministic", ACTORS_DIR)
        self.assertIsInstance(profile, ActorProfile)
        self.assertEqual(profile.name, "wildberries_user_deterministic")
        self.assertEqual(profile.proxy_ref, "")
        self.assertEqual(profile.fingerprint.timezone, "Europe/Moscow")
        self.assertEqual(profile.fingerprint.screen.width, 1920)
        self.assertEqual(profile.credentials.login_env, "WILBERRIES_USER_DETERMINISTIC_PHONE")
        self.assertEqual(profile.behavior, "configs/behavior/wildberries.yaml")
        self.assertIn("Arial", profile.fingerprint.fonts)

    def test_load_wildberries_user_llm_profile(self) -> None:
        profile = load_actor("wildberries_user_llm", ACTORS_DIR)
        self.assertEqual(profile.name, "wildberries_user_llm")
        self.assertEqual(profile.fingerprint.screen.width, 1680)
        self.assertEqual(profile.credentials.password_env, "ACTOR_WB_USER_LLM_PASSWORD")

    def test_load_missing_profile_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_actor("nonexistent", ACTORS_DIR)

    def test_load_malformed_profile_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text('{"name": "bad"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_actor("bad", path.parent)


class TestActorRun(unittest.TestCase):
    def _run(self, coro: object) -> object:
        return asyncio.run(coro)  # type: ignore[arg-type]

    def test_actor_cycle_completes_and_logs(self) -> None:
        async def scenario() -> tuple[str, str]:
            with tempfile.TemporaryDirectory() as tmp:
                db_path = Path(tmp) / "test.db"
                await init_db(db_path)
                db = await open_connection(db_path)
                try:
                    profile = load_actor("wildberries_user_deterministic", ACTORS_DIR)
                    actor = Actor(profile, db=db)
                    await actor.run()
                    self.assertEqual(actor.state, ActorState.IDLE)
                    return actor.logger.session_id, actor.website  # type: ignore[union-attr]
                finally:
                    await db.close()

        session_id, website = self._run(scenario())  # type: ignore[misc]
        self.assertEqual(website, "seller.wildberries.ru")
        self.assertTrue(session_id)

    def test_actor_stop_cancels(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                db_path = Path(tmp) / "test.db"
                await init_db(db_path)
                db = await open_connection(db_path)
                try:
                    profile = load_actor("wildberries_user_llm", ACTORS_DIR)
                    actor = Actor(profile, db=db)
                    actor.stop()
                    await actor.run()
                finally:
                    await db.close()

        # stop set before run -> run exits immediately after logging start
        self._run(scenario())

    def test_actor_from_name(self) -> None:
        actor = Actor.from_name("wildberries_user_deterministic")
        self.assertEqual(actor.name, "wildberries_user_deterministic")
        self.assertEqual(actor.website, "seller.wildberries.ru")


if __name__ == "__main__":
    unittest.main()
