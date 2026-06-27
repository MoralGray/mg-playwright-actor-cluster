from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from swarm.actor import Actor
from swarm.profile import ActorProfile, load_actor
from swarm.proxy import PROXIES_PATH, Proxy, ProxyPool, load_proxies
from swarm.state import ActorState

logger = logging.getLogger(__name__)

MAX_SESSIONS_PER_KEY = 1


def _no_proxy() -> bool:
    """Return True when NO_PROXY env var is set (1/true): skip pool entirely."""
    return os.environ.get("NO_PROXY", "").lower() in ("1", "true")


@dataclass(frozen=True, slots=True)
class ActorStatus:
    name: str
    state: str
    alive: bool
    paused: bool
    proxy_endpoint: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "state": self.state,
            "alive": self.alive,
            "paused": self.paused,
            "proxy_endpoint": self.proxy_endpoint,
        }


class SwarmManager:
    """Owns the actor registry, proxy pool and per-website session limits."""

    def __init__(
        self,
        db: aiosqlite.Connection | None = None,
        proxies: list[Proxy] | None = None,
        proxies_path: Path = PROXIES_PATH,
        max_sessions_per_key: int = MAX_SESSIONS_PER_KEY,
    ) -> None:
        self._db = db
        self._actors: dict[str, Actor] = {}
        self._lock = asyncio.Lock()
        self._pool = ProxyPool(proxies if proxies is not None else load_proxies(proxies_path))
        self._assignments: dict[str, Proxy] = {}
        self._slots: dict[tuple[str, str], asyncio.Semaphore] = {}
        self._max_per_key = max_sessions_per_key

    @property
    def db(self) -> aiosqlite.Connection | None:
        return self._db

    def set_db(self, db: aiosqlite.Connection) -> None:
        self._db = db

    @property
    def proxy_pool(self) -> ProxyPool:
        return self._pool

    def knows(self, name: str) -> bool:
        return name in self._actors

    def names(self) -> Iterable[str]:
        return self._actors.keys()

    def get(self, name: str) -> Actor | None:
        return self._actors.get(name)

    def list_status(self) -> list[ActorStatus]:
        statuses: list[ActorStatus] = []
        for name, actor in self._actors.items():
            task = actor._task
            alive = task is not None and not task.done()
            statuses.append(
                ActorStatus(
                    name=name,
                    state=actor.state.value,
                    alive=alive,
                    paused=actor.is_paused(),
                    proxy_endpoint=actor.proxy_endpoint,
                )
            )
        return statuses

    def register(self, profile: ActorProfile) -> Actor:
        if profile.name in self._actors:
            return self._actors[profile.name]
        actor = Actor(profile, db=self._db, manager=self)
        self._actors[profile.name] = actor
        return actor

    def register_from_name(self, name: str) -> Actor:
        return self.register(load_actor(name))

    def current_proxy(self, name: str) -> Proxy | None:
        return self._assignments.get(name)

    async def assign_proxy(self, profile: ActorProfile) -> Proxy:
        proxy = await self._pool.acquire()
        self._assignments[profile.name] = proxy
        actor = self._actors.get(profile.name)
        if actor is not None:
            actor.proxy_endpoint = proxy.endpoint
        return proxy

    def _slot_for(self, website: str, proxy_ref: str) -> asyncio.Semaphore:
        key = (website, "noproxy" if _no_proxy() else proxy_ref)
        sem = self._slots.get(key)
        if sem is None:
            sem = asyncio.Semaphore(self._max_per_key)
            self._slots[key] = sem
        return sem

    async def acquire_slot(self, website: str, proxy_ref: str) -> asyncio.Semaphore:
        sem = self._slot_for(website, proxy_ref)
        await sem.acquire()
        return sem

    def release_slot(self, sem: asyncio.Semaphore) -> None:
        with contextlib.suppress(ValueError):
            sem.release()

    async def spawn_actor(self, name: str) -> Actor:
        async with self._lock:
            actor = self._actors.get(name)
            if actor is None:
                actor = self.register_from_name(name)
            if actor.proxy_endpoint is None and not _no_proxy():
                proxy = await self.assign_proxy(actor.profile)
                actor.proxy_endpoint = proxy.endpoint
            task = actor.spawn()
            logger.info("spawned actor %s as task %s", name, task.get_name())
            return actor

    async def stop_actor(self, name: str) -> Actor:
        # Acquire the lock only to read/cancel the task; release it before
        # awaiting the task so concurrent callers (e.g. rotate_proxy) and the
        # actor's own cleanup paths that touch the manager do not deadlock.
        async with self._lock:
            actor = self._require(name)
            actor.stop()
            task = actor._task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except TimeoutError:
                task.cancel()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("actor %s stop raised", name)
        async with self._lock:
            await self._release_assignment(name)
            return actor

    async def restart_actor(self, name: str) -> Actor:
        if name in self._actors:
            await self.stop_actor(name)
            old = self._actors.pop(name)
            old.resume()
        return await self.spawn_actor(name)

    async def pause_actor(self, name: str) -> Actor:
        async with self._lock:
            actor = self._require(name)
            actor.pause()
            return actor

    async def resume_actor(self, name: str) -> Actor:
        async with self._lock:
            actor = self._require(name)
            actor.resume()
            return actor

    async def rotate_proxy(self, name: str) -> Proxy | None:
        """Mark the actor's current proxy banned and reassign a fresh one.

        The actor task is restarted so the new browser context uses the new
        proxy endpoint. Raises ProxyUnavailable when the pool is exhausted.
        Returns None when NO_PROXY is set (no pool interaction).
        """
        async with self._lock:
            actor = self._require(name)
            if _no_proxy():
                actor.proxy_endpoint = None
                logger.info("NO_PROXY set; restart actor %s without proxy", name)
                await self.restart_actor(name)
                return None
            old = self._assignments.get(name)
            if old is not None:
                await self._pool.mark_banned(old.ref)
                logger.warning("proxy %s banned for actor %s", old.ref, name)
            proxy = await self._pool.acquire()
            self._assignments[name] = proxy
            actor.proxy_endpoint = proxy.endpoint
            logger.info("rotated actor %s to proxy %s", name, proxy.ref)
        await self.restart_actor(name)
        return proxy

    async def stop_all(self) -> None:
        names = list(self._actors.keys())
        for name in names:
            try:
                await self.stop_actor(name)
            except Exception:
                logger.exception("stop_all failed for %s", name)

    async def _release_assignment(self, name: str) -> None:
        proxy = self._assignments.pop(name, None)
        if proxy is not None:
            await self._pool.release(proxy)

    def _require(self, name: str) -> Actor:
        actor = self._actors.get(name)
        if actor is None:
            raise KeyError(f"actor not registered: {name}")
        return actor


def state_value(actor: Actor | None) -> str:
    return ActorState.IDLE.value if actor is None else actor.state.value


__all__ = ["MAX_SESSIONS_PER_KEY", "ActorStatus", "SwarmManager"]
