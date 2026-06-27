from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

from orc.server import create_app

ACTORS_DIR = Path(__file__).resolve().parent.parent.parent / "configs" / "actors"


def _wait_until(predicate, timeout: float = 3.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestServerRoutes(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "test.db"
        self._db_path = db_path
        # Patch DB_PATH so the server uses an isolated db.
        from db import sqlite as sqlite_mod

        self._orig_path = sqlite_mod.DB_PATH
        sqlite_mod.DB_PATH = db_path
        from db import logger as logger_mod

        self._orig_logger_path = logger_mod.DB_PATH
        logger_mod.DB_PATH = db_path
        self.client = TestClient(create_app())
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        from db import sqlite as sqlite_mod

        sqlite_mod.DB_PATH = self._orig_path
        from db import logger as logger_mod

        logger_mod.DB_PATH = self._orig_logger_path
        self._tmp.cleanup()

    def test_health(self) -> None:
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})

    def test_actor_start_stop_lifecycle(self) -> None:
        self.assertEqual(self.client.get("/actors").json(), [])
        r = self.client.post("/actors/wildberries_user_llm/start")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["started"])
        r = self.client.get("/actors/wildberries_user_llm")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "wildberries_user_llm")
        self.assertFalse(r.json()["done"])
        r = self.client.post("/actors/wildberries_user_llm/stop")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["stopped"])
        self.assertTrue(
            _wait_until(lambda: self.client.get("/actors/wildberries_user_llm").json()["done"])
        )

    def test_actor_pause_resume(self) -> None:
        self.client.post("/actors/wildberries_user_llm/start")
        r = self.client.post("/actors/wildberries_user_llm/pause")
        self.assertTrue(r.json()["paused"])
        self.assertTrue(self.client.get("/actors/wildberries_user_llm").json()["paused"])
        r = self.client.post("/actors/wildberries_user_llm/resume")
        self.assertTrue(r.json()["resumed"])
        self.assertFalse(self.client.get("/actors/wildberries_user_llm").json()["paused"])
        self.client.post("/actors/wildberries_user_llm/stop")

    def test_restart_actor(self) -> None:
        self.client.post("/actors/wildberries_user_llm/start")
        r = self.client.post("/actors/wildberries_user_llm/restart")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["restarted"])
        self.client.post("/actors/wildberries_user_llm/stop")

    def test_unknown_actor_returns_404(self) -> None:
        r = self.client.get("/actors/ghost")
        self.assertEqual(r.status_code, 404)
        r = self.client.post("/actors/ghost/stop")
        self.assertEqual(r.status_code, 404)

    def test_input_endpoint_sets_value_on_actor(self) -> None:
        self.client.post("/actors/wildberries_user_llm/start")
        r = self.client.post(
            "/actors/wildberries_user_llm/input",
            json={"key": "ACTOR_CODE", "value": "98765"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["set"])
        actor = self.client.app.state.manager.get("wildberries_user_llm")
        self.assertEqual(actor._inputs["ACTOR_CODE"], "98765")
        self.client.post("/actors/wildberries_user_llm/stop")

    def test_input_endpoint_unknown_actor_404(self) -> None:
        r = self.client.post(
            "/actors/ghost/input",
            json={"key": "ACTOR_CODE", "value": "1"},
        )
        self.assertEqual(r.status_code, 404)

    def test_enqueue_task_creates_session(self) -> None:
        r = self.client.post(
            "/tasks",
            json={
                "actor_id": "wildberries_user_llm",
                "website": "seller.wildberries.ru",
                "action": "login",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "pending")
        task_id = r.json()["task_id"]
        self.assertTrue(task_id)

        self.assertTrue(
            _wait_until(
                lambda: any(
                    a["name"] == "wildberries_user_llm" for a in self.client.get("/actors").json()
                )
            )
        )
        # Stop the spawned actor and wait for the task to finish.
        self.client.post("/actors/wildberries_user_llm/stop")
        self.assertTrue(
            _wait_until(
                lambda: any(
                    s["actor_id"] == "wildberries_user_llm"
                    for s in self.client.get("/sessions").json()
                )
            )
        )
        sessions = [
            s
            for s in self.client.get("/sessions").json()
            if s["actor_id"] == "wildberries_user_llm"
        ]
        self.assertTrue(sessions)
        self.assertIn(sessions[0]["status"], {"running", "success", "failed"})


if __name__ == "__main__":
    unittest.main()
