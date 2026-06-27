from __future__ import annotations

import asyncio
import contextlib
import logging
import random

import aiosqlite

from db.sqlite import finish_session, start_session
from orc.task_queue import (
    TASK_DONE,
    TASK_FAILED,
    TASK_RUNNING,
    Task,
    TaskQueue,
    persist_task,
)
from swarm.manager import SwarmManager

logger = logging.getLogger(__name__)

# Random inter-batch pause bounds (seconds) to rate-limit bursts.
BATCH_PAUSE_MIN = 1.0
BATCH_PAUSE_MAX = 5.0


class TaskRunner:
    """Pulls Tasks from the queue and dispatches them to the SwarmManager.

    Each task: persist status running, start a session, spawn/restart the actor,
    await the actor task, then mark task done/failed and finish the session.
    """

    def __init__(
        self,
        queue: TaskQueue,
        manager: SwarmManager,
        db: aiosqlite.Connection,
        rng: random.Random | None = None,
        batch_pause: tuple[float, float] = (BATCH_PAUSE_MIN, BATCH_PAUSE_MAX),
    ) -> None:
        self.queue = queue
        self.manager = manager
        self.db = db
        self.rng = rng or random.Random()
        self.batch_pause = batch_pause
        self._task: asyncio.Task[None] | None = None

    def start(self) -> asyncio.Task[None]:
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self.run(), name="task-runner")
        return self._task

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def run(self) -> None:
        while True:
            try:
                task = await self.queue.dispatch()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("queue dispatch failed")
                continue
            try:
                await self._handle(task)
            except asyncio.CancelledError:
                raise
            except Exception:
                # One bad task must not kill the processing loop.
                logger.exception("task %s handling failed", getattr(task, "id", "?"))
            await self._batch_pause()

    async def _batch_pause(self) -> None:
        lo, hi = self.batch_pause
        if hi <= 0:
            return
        delay = self.rng.uniform(max(0.0, lo), hi)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _handle(self, task: Task) -> None:
        await persist_task(self.db, task, status=TASK_RUNNING)
        session_id = await start_session(task.actor_id, task.website)
        try:
            await self.manager.spawn_actor(task.actor_id)
            actor = self.manager.get(task.actor_id)
            if actor is None or actor._task is None:
                raise RuntimeError(f"actor {task.actor_id} spawn failed")
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(actor._task)
        except Exception:
            logger.exception("task %s failed", task.id)
            # Persist failed state here (not via re-raise) so the tasks row
            # does not remain TASK_RUNNING permanently.
            with contextlib.suppress(Exception):
                await persist_task(self.db, task, status=TASK_FAILED)
            with contextlib.suppress(Exception):
                await finish_session(session_id, "failed", 1)
            return
        await persist_task(self.db, task, status=TASK_DONE)
        with contextlib.suppress(Exception):
            await finish_session(session_id, "success", 0)


__all__ = ["TaskRunner"]
