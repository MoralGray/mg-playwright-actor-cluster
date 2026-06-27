from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import aiosqlite

from db.sqlite import (
    DB_PATH,
    _now,
    insert_session,
    open_connection,
)

if TYPE_CHECKING:
    from playwright.async_api import Page

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def _default_screenshots_dir() -> Path:
    """Fallback screenshots dir when a caller does not pass one.

    The Actor always supplies a run-scoped ``screenshots_dir`` (either a
    pipeline-provided ``RUN_DIR`` or a freshly-created timestamped run
    folder). This default only keeps the Logger usable in isolation
    (e.g. tests, standalone scripts).
    """
    return OUTPUT_DIR


class Logger:
    def __init__(
        self,
        session_id: str,
        actor_id: str,
        db: aiosqlite.Connection,
        screenshots_dir: Path | None = None,
    ) -> None:
        self.session_id = session_id
        self.actor_id = actor_id
        self.db = db
        self.screenshots_dir = (
            screenshots_dir if screenshots_dir is not None else _default_screenshots_dir()
        )
        self._errors = 0

    async def step(
        self,
        step_name: str,
        level: str,
        message: str,
        screenshot: str | None = None,
    ) -> None:
        if level == "error":
            self._errors += 1
        await self.db.execute(
            "INSERT INTO logs "
            "(session_id, actor_id, step_name, level, message, screenshot, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                self.session_id,
                self.actor_id,
                step_name,
                level,
                message,
                screenshot,
                _now(),
            ),
        )
        await self.db.commit()

    async def screenshot(self, page: Page, step_name: str) -> str:
        session_dir = self.screenshots_dir / self.session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        path = session_dir / f"{step_name}.png"
        await page.screenshot(path=str(path), full_page=True)
        return str(path)

    async def fail(self, errors: int | None = None) -> None:
        count = self._errors if errors is None else errors
        await self._finish("failed", count)

    async def success(self) -> None:
        await self._finish("success", self._errors)

    async def _finish(self, status: str, errors: int) -> None:
        await self.db.execute(
            "UPDATE sessions SET status = ?, errors = ?, finished_at = ? WHERE id = ?",
            (status, errors, _now(), self.session_id),
        )
        await self.db.commit()


async def create_logger(
    actor_id: str,
    website: str,
    db: aiosqlite.Connection | None = None,
    screenshots_dir: Path | None = None,
    db_path: Path = DB_PATH,
) -> Logger:
    owns_db = db is None
    if owns_db:
        db = await open_connection(db_path)
    assert db is not None
    session_id = str(uuid4())
    await insert_session(
        db,
        {
            "id": session_id,
            "actor_id": actor_id,
            "website": website,
            "status": "running",
            "errors": 0,
            "started_at": _now(),
            "finished_at": None,
        },
    )
    return Logger(session_id, actor_id, db, screenshots_dir)
