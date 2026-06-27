from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

DB_PATH = Path(__file__).resolve().parent.parent / "swarm.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    actor_id TEXT,
    step_name TEXT,
    level TEXT,
    message TEXT,
    screenshot TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    actor_id TEXT,
    website TEXT,
    status TEXT,
    errors INTEGER,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    actor_id TEXT,
    website TEXT,
    action TEXT,
    status TEXT,
    created_at TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def init_db(path: Path = DB_PATH) -> None:
    async with aiosqlite.connect(path) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def open_connection(path: Path = DB_PATH) -> aiosqlite.Connection:
    db = await aiosqlite.connect(path)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.commit()
    return db


class _ConnectionContext:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def __aenter__(self) -> aiosqlite.Connection:
        self._db = await open_connection(self._path)
        return self._db

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None


def connection(path: Path = DB_PATH) -> AsyncIterator[aiosqlite.Connection]:
    return _ConnectionContext(path)


async def insert_session(db: aiosqlite.Connection, session: dict[str, Any]) -> None:
    await db.execute(
        "INSERT OR REPLACE INTO sessions "
        "(id, actor_id, website, status, errors, started_at, finished_at) "
        "VALUES (:id, :actor_id, :website, :status, :errors, :started_at, :finished_at)",
        session,
    )
    await db.commit()


async def insert_log(db: aiosqlite.Connection, log: dict[str, Any]) -> None:
    log.setdefault("created_at", _now())
    await db.execute(
        "INSERT INTO logs "
        "(session_id, actor_id, step_name, level, message, screenshot, created_at) "
        "VALUES (:session_id, :actor_id, :step_name, :level, :message, :screenshot, :created_at)",
        log,
    )
    await db.commit()


async def insert_task(db: aiosqlite.Connection, task: dict[str, Any]) -> None:
    task.setdefault("created_at", _now())
    await db.execute(
        "INSERT OR REPLACE INTO tasks "
        "(id, actor_id, website, action, status, created_at) "
        "VALUES (:id, :actor_id, :website, :action, :status, :created_at)",
        task,
    )
    await db.commit()


async def start_session(actor_id: str, website: str, path: Path = DB_PATH) -> str:
    session_id = str(uuid4())
    async with connection(path) as db:
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
    return session_id


async def finish_session(
    session_id: str,
    status: str,
    errors: int,
    path: Path = DB_PATH,
) -> None:
    async with connection(path) as db:
        await db.execute(
            "UPDATE sessions SET status = ?, errors = ?, finished_at = ? WHERE id = ?",
            (status, errors, _now(), session_id),
        )
        await db.commit()


async def log_step(
    session_id: str,
    actor_id: str,
    step_name: str,
    level: str,
    message: str,
    screenshot: str | None = None,
    path: Path = DB_PATH,
) -> None:
    async with connection(path) as db:
        await insert_log(
            db,
            {
                "session_id": session_id,
                "actor_id": actor_id,
                "step_name": step_name,
                "level": level,
                "message": message,
                "screenshot": screenshot,
            },
        )


async def query_sessions(
    status: str | None = None,
    path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    async with connection(path) as db:
        if status is None:
            cursor = await db.execute("SELECT * FROM sessions ORDER BY started_at DESC")
        else:
            cursor = await db.execute(
                "SELECT * FROM sessions WHERE status = ? ORDER BY started_at DESC",
                (status,),
            )
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in rows]


async def query_logs(
    session_id: str | None = None,
    path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    async with connection(path) as db:
        if session_id is None:
            cursor = await db.execute("SELECT * FROM logs ORDER BY id ASC")
        else:
            cursor = await db.execute(
                "SELECT * FROM logs WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            )
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in rows]


async def query_logs_by_actor(
    db: aiosqlite.Connection,
    actor_id: str,
) -> list[dict[str, Any]]:
    cursor = await db.execute(
        "SELECT * FROM logs WHERE actor_id = ? ORDER BY id ASC",
        (actor_id,),
    )
    rows = await cursor.fetchall()
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in rows]
