from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from db.sqlite import init_db, open_connection, query_sessions
from orc.runner import TaskRunner
from orc.task_queue import (
    TaskQueue,
    enqueue_and_persist,
    load_pending_tasks,
    new_task,
)
from swarm.manager import SwarmManager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    db = await open_connection()
    manager = SwarmManager(db=db)
    queue = TaskQueue()
    runner = TaskRunner(queue, manager, db)
    app.state.db = db
    app.state.manager = manager
    app.state.queue = queue
    app.state.runner = runner
    # Re-enqueue any tasks that were persisted as pending before the previous
    # shutdown so they are not silently lost on server restart.
    pending = await load_pending_tasks(db)
    for task in pending:
        await enqueue_and_persist(queue, db, task)
    runner.start()
    try:
        yield
    finally:
        await runner.stop()
        await manager.stop_all()
        await db.close()


class TaskCreate(BaseModel):
    actor_id: str
    website: str = ""
    action: str = ""


class InputBody(BaseModel):
    key: str
    value: str


def create_app() -> FastAPI:
    app = FastAPI(title="Human Bot Swarm", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    def _manager() -> SwarmManager:
        return app.state.manager

    @app.get("/actors")
    async def list_actors() -> list[dict[str, object]]:
        return [s.as_dict() for s in _manager().list_status()]

    @app.get("/actors/{name}")
    async def actor_detail(name: str) -> dict[str, object]:
        actor = _manager().get(name)
        if actor is None:
            raise HTTPException(status_code=404, detail=f"actor not found: {name}")
        return {
            "name": name,
            "state": actor.state.value,
            "paused": actor.is_paused(),
            "task": actor._task.get_name() if actor._task else None,
            "done": actor._task.done() if actor._task else True,
            "proxy_endpoint": actor.proxy_endpoint,
        }

    @app.post("/actors/{name}/start")
    async def actor_start(name: str) -> dict[str, object]:
        await _manager().spawn_actor(name)
        return {"name": name, "started": True}

    @app.post("/actors/{name}/stop")
    async def actor_stop(name: str) -> dict[str, object]:
        try:
            await _manager().stop_actor(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"name": name, "stopped": True}

    @app.post("/actors/{name}/restart")
    async def actor_restart(name: str) -> dict[str, object]:
        await _manager().restart_actor(name)
        return {"name": name, "restarted": True}

    @app.post("/actors/{name}/pause")
    async def actor_pause(name: str) -> dict[str, object]:
        try:
            await _manager().pause_actor(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"name": name, "paused": True}

    @app.post("/actors/{name}/resume")
    async def actor_resume(name: str) -> dict[str, object]:
        try:
            await _manager().resume_actor(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"name": name, "resumed": True}

    @app.post("/actors/{name}/input")
    async def actor_input(name: str, payload: InputBody) -> dict[str, object]:
        actor = _manager().get(name)
        if actor is None:
            raise HTTPException(status_code=404, detail=f"actor not found: {name}")
        actor.set_input(payload.key, payload.value)
        return {"name": name, "key": payload.key, "set": True}

    @app.post("/tasks")
    async def enqueue_task(payload: TaskCreate) -> dict[str, object]:
        task = new_task(payload.actor_id, payload.website, payload.action)
        await enqueue_and_persist(
            app.state.queue,
            app.state.db,
            task,
        )
        return {"task_id": task.id, "status": "pending"}

    @app.get("/sessions")
    async def list_sessions(status: str | None = None) -> list[dict[str, object]]:
        return await query_sessions(status)

    @app.get("/proxies")
    async def list_proxies() -> list[dict[str, object]]:
        return [p.as_dict() for p in _manager().proxy_pool.status()]

    return app
