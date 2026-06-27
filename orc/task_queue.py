from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4

import aiosqlite

from db.sqlite import _now, open_connection

TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_DONE = "done"
TASK_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    actor_id: str
    website: str
    action: str
    status: str = TASK_PENDING
    created_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def new_task(actor_id: str, website: str, action: str) -> Task:
    return Task(
        id=str(uuid4()),
        actor_id=actor_id,
        website=website,
        action=action,
        status=TASK_PENDING,
        created_at="",
    )


class TaskQueue:
    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[Task] = asyncio.Queue(maxsize=maxsize)

    async def enqueue(self, task: dict[str, Any]) -> None:
        await self._queue.put(_coerce_task(task))

    async def enqueue_task(self, task: Task) -> None:
        await self._queue.put(task)

    async def dispatch(self) -> Task:
        return await self._queue.get()

    def empty(self) -> bool:
        return self._queue.empty()

    def size(self) -> int:
        return self._queue.qsize()


def _coerce_task(data: dict[str, Any]) -> Task:
    if isinstance(data, Task):
        return data
    return Task(
        id=data.get("id") or str(uuid4()),
        actor_id=data["actor_id"],
        website=data.get("website", ""),
        action=data.get("action", ""),
        status=data.get("status", TASK_PENDING),
        created_at=data.get("created_at", ""),
    )


async def persist_task(
    db: aiosqlite.Connection,
    task: Task,
    status: str | None = None,
) -> None:
    row = task.as_dict()
    if status is not None:
        row["status"] = status
    if not row["created_at"]:
        row["created_at"] = _now()
    await db.execute(
        "INSERT OR REPLACE INTO tasks "
        "(id, actor_id, website, action, status, created_at) "
        "VALUES (:id, :actor_id, :website, :action, :status, :created_at)",
        row,
    )
    await db.commit()


async def enqueue_and_persist(
    queue: TaskQueue,
    db: aiosqlite.Connection,
    task: Task,
) -> Task:
    if not task.created_at:
        task = Task(
            id=task.id,
            actor_id=task.actor_id,
            website=task.website,
            action=task.action,
            status=TASK_PENDING,
            created_at=_now(),
        )
    await persist_task(db, task, status=TASK_PENDING)
    await queue.enqueue_task(task)
    return task


async def load_pending_tasks(db: aiosqlite.Connection) -> list[Task]:
    cursor = await db.execute(
        "SELECT id, actor_id, website, action, status, created_at FROM tasks "
        "WHERE status = ? ORDER BY created_at ASC",
        (TASK_PENDING,),
    )
    rows = await cursor.fetchall()
    return [
        Task(
            id=row[0],
            actor_id=row[1],
            website=row[2],
            action=row[3],
            status=row[4],
            created_at=row[5],
        )
        for row in rows
    ]


__all__ = [
    TASK_DONE,
    TASK_FAILED,
    TASK_PENDING,
    TASK_RUNNING,
    Task,
    TaskQueue,
    enqueue_and_persist,
    load_pending_tasks,
    new_task,
    open_connection,
    persist_task,
]
