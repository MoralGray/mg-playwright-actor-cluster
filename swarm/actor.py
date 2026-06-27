from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import aiosqlite

from db.logger import Logger, create_logger
from db.sqlite import open_connection
from engine.ban import BanDetected
from engine.executor import ActionExecutor
from engine.llm_fallback import LLMClient
from engine.yaml_reader import (
    Behavior,
    apply_variables,
    build_var_map,
    load_behavior_or_none,
)
from swarm.profile import ActorProfile, load_actor
from swarm.state import ActorState, next_state

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page

    from swarm.manager import SwarmManager

logger = logging.getLogger(__name__)

StateHandler = Callable[[], Awaitable[ActorState]]

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def _actor_screenshots_dir() -> Path:
    """Resolve the screenshots base dir for this actor run.

    Pipeline scripts (e.g. ``wb-analytics``) set ``RUN_DIR`` to group every
    artifact of one pipeline execution together; honor it as an override so
    actor screenshots nest inside the pipeline's run folder. Otherwise create
    a fresh timestamped run folder so standalone API runs do not scatter
    bare session-UUID folders at the ``output/`` root.
    """
    run_dir = os.environ.get("RUN_DIR")
    if run_dir:
        return Path(run_dir)
    stamp = datetime.now().strftime("run-%d-%m-%Y-%H-%M-%S-%f")
    path = OUTPUT_DIR / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


class Actor:
    def __init__(
        self,
        profile: ActorProfile,
        db: aiosqlite.Connection | None = None,
        manager: SwarmManager | None = None,
    ) -> None:
        self.profile = profile
        self.db = db
        self.manager = manager
        self.state = ActorState.IDLE
        self.logger: Logger | None = None
        self.page: Page | None = None
        self._browser: Browser | None = None
        self._browser_context: BrowserContext | None = None
        self.llm: LLMClient | None = None
        self._behavior: Behavior | None = None
        self.proxy_endpoint: str | None = None
        self._slot: asyncio.Semaphore | None = None
        self._rotate_task: asyncio.Task[None] | None = None
        self._cookies_path: Path | None = None
        self._stop = asyncio.Event()
        self._pause = asyncio.Event()
        self._pause.set()  # not paused by default
        self._inputs: dict[str, str] = {}
        self._input_event: asyncio.Event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        # Serializes run() entry against the previous run's cleanup. Without
        # this, a re-spawn on the same Actor instance could start a new
        # browser context while the old run()'s finally block is still
        # tearing down self.page / self._browser / self._browser_context /
        # self.logger, leading to a race on the shared mutable state.
        self._lifecycle_lock = asyncio.Lock()
        self._handlers: dict[ActorState, StateHandler] = {
            ActorState.IDLE: self._on_idle,
            ActorState.LOGIN: self._on_login,
            ActorState.NAVIGATE: self._on_navigate,
            ActorState.ACTION: self._on_action,
            ActorState.EXTRACT: self._on_extract,
            ActorState.REPORT: self._on_report,
        }

    @classmethod
    def from_name(cls, name: str, db: aiosqlite.Connection | None = None) -> Actor:
        return cls(load_actor(name), db=db)

    @property
    def name(self) -> str:
        return self.profile.name

    @property
    def website(self) -> str:
        return urlparse(self.profile.credentials.url).netloc

    def spawn(self) -> asyncio.Task[None]:
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self.run(), name=f"actor:{self.name}")
        return self._task

    def stop(self) -> None:
        self._stop.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def pause(self) -> None:
        self._pause.clear()

    def resume(self) -> None:
        self._pause.set()

    def is_paused(self) -> bool:
        return not self._pause.is_set()

    def set_input(self, key: str, value: str) -> None:
        """Provide an out-of-band input value (e.g. SMS code) that a
        ``wait_input`` step is waiting on.
        """
        self._inputs[key] = value
        self._input_event.set()

    def _current_proxy_ref(self) -> str:
        if self.manager is not None:
            proxy = self.manager.current_proxy(self.name)
            if proxy is not None:
                return proxy.ref
        return ""

    async def _gate(self) -> None:
        # Block until either the pause gate opens or a stop is requested.
        # Uses asyncio.Event.wait() instead of a polling busy-loop.
        pause_wait = asyncio.ensure_future(self._pause.wait())
        stop_wait = asyncio.ensure_future(self._stop.wait())
        try:
            await asyncio.wait({pause_wait, stop_wait}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for fut in (pause_wait, stop_wait):
                if not fut.done():
                    fut.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await fut

    async def run(self) -> None:
        # Acquire the lifecycle lock for the whole run so a re-spawn on the
        # same instance waits for the previous run's cleanup (finally block)
        # to finish before re-initializing shared mutable state.
        async with self._lifecycle_lock:
            owns_db = self.db is None
            if owns_db:
                self.db = await open_connection()
            screenshots_dir = _actor_screenshots_dir()
            self.logger = await create_logger(
                self.name,
                self.website,
                self.db,
                screenshots_dir=screenshots_dir,
            )
            await self.logger.step(
                "run_dir",
                "info",
                f"screenshots dir: {screenshots_dir}",
            )

            if self.manager is not None:
                proxy_ref = self._current_proxy_ref()
                self._slot = await self.manager.acquire_slot(self.website, proxy_ref)
                await self.logger.step(
                    "slot", "info", f"acquired session slot for {self.website}/{proxy_ref}"
                )

            from playwright.async_api import async_playwright

            pw = await async_playwright().start()
            try:
                await self.logger.step(
                    "lifecycle", "info", f"actor {self.name} started in {self.state.value}"
                )

                from browser.context import create_context as create_browser_context

                (
                    self._browser,
                    self._browser_context,
                    init_rf,
                    self._cookies_path,
                ) = await create_browser_context(
                    pw,
                    self.profile,
                    proxy=self.proxy_endpoint,
                    headless=os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() != "false",
                )
                page = await self._browser_context.new_page()
                self.page = page
                # Re-inject the stealth script via raw CDP
                # (Page.addScriptToEvaluateOnNewDocument) as a second defense
                # layer in addition to context.add_init_script. CDP injection
                # runs before page scripts with runImmediately semantics.
                with contextlib.suppress(Exception):
                    from browser.fingerprint import build_init_script

                    cdp = await self._browser_context.new_cdp_session(page)
                    await cdp.send(
                        "Page.addScriptToEvaluateOnNewDocument",
                        {"source": build_init_script(init_rf)},
                    )
                    await self.logger.step(
                        "stealth", "info", "CDP init script injected via Page domain"
                    )
                no_proxy = os.environ.get("NO_PROXY", "").lower() in ("1", "true")
                if no_proxy:
                    await self.logger.step("proxy", "info", "NO_PROXY set; running without proxy")
                elif self.proxy_endpoint is not None:
                    await self.logger.step("proxy", "info", f"using proxy {self.proxy_endpoint}")

                while not self._stop.is_set():
                    await self._gate()
                    if self._stop.is_set():
                        break
                    handler = self._handlers[self.state]
                    new_state = await handler()
                    if self._stop.is_set():
                        break
                    old_state = self.state
                    self.state = new_state
                    await self.logger.step(
                        "state", "info", f"{old_state.value} -> {new_state.value}"
                    )
                    if new_state == ActorState.IDLE:
                        break
                if self._stop.is_set():
                    await self.logger.step("lifecycle", "info", "actor stopped")
                else:
                    await self.logger.step("lifecycle", "info", "actor cycle complete")
                await self.logger.success()
            except asyncio.CancelledError:
                if self.logger is not None:
                    await self.logger.step("lifecycle", "warn", "actor cancelled")
                    await self.logger.fail()
                raise
            except Exception as exc:
                logger.exception("actor %s crashed", self.name)
                if self.logger is not None:
                    await self.logger.step("lifecycle", "error", f"actor crashed: {exc!r}")
                    await self.logger.fail()
                raise
            finally:
                self.page = None
                self._browser_context = None
                if self._browser is not None:
                    with contextlib.suppress(Exception):
                        await self._browser.close()
                    self._browser = None
                with contextlib.suppress(Exception):
                    await pw.stop()
                if self._slot is not None and self.manager is not None:
                    self.manager.release_slot(self._slot)
                    self._slot = None
                if owns_db and self.db is not None:
                    await self.db.close()

    async def _on_idle(self) -> ActorState:
        await self.logger.step("idle", "info", "idle -> starting cycle")
        return next_state(ActorState.IDLE)

    async def _on_login(self) -> ActorState:
        creds = self.profile.credentials
        await self.logger.step("login", "info", f"login {creds.url} as {creds.login}")
        return next_state(ActorState.LOGIN)

    async def _on_navigate(self) -> ActorState:
        await self.logger.step("navigate", "info", f"navigate target: {self.website}")
        return next_state(ActorState.NAVIGATE)

    async def _on_action(self) -> ActorState:
        cookies_path = Path("cookies") / f"{self.name}.json"
        cookies_exist = cookies_path.exists()
        cookies_size = cookies_path.stat().st_size if cookies_exist else 0
        has_cookies = cookies_exist and cookies_size > 0
        logger.warning(
            "cookies check: path=%s exists=%s size=%d",
            cookies_path,
            cookies_exist,
            cookies_size,
        )
        behavior_path = (
            "configs/behavior/wildberries_loggedin.yaml" if has_cookies else self.profile.behavior
        )
        behavior = self._behavior or load_behavior_or_none(behavior_path)
        if has_cookies and behavior:
            await self.logger.step(
                "cookies", "info", f"cookies found at {cookies_path}; skipping login"
            )
        if behavior is not None and self._behavior is None:
            behavior = apply_variables(behavior, build_var_map(self.profile))
        self._behavior = behavior
        if behavior is None:
            await self.logger.step(
                "action", "warn", f"behavior file missing: {self.profile.behavior}"
            )
            return next_state(ActorState.ACTION)
        if self.page is None or self.logger is None:
            await self.logger.step(
                "action",
                "info",
                f"behavior {behavior.name} loaded (no page attached; skipped)",
            )
            return next_state(ActorState.ACTION)
        executor = ActionExecutor(
            page=self.page,
            behavior=behavior,
            logger=self.logger,
            llm=self.llm,
            on_ban=self._on_ban,
            inputs=self._inputs,
            input_event=self._input_event,
            stop_event=self._stop,
        )
        try:
            report = await executor.run()
        except BanDetected as exc:
            await self.logger.step(
                "ban",
                "error",
                f"ban {exc.status} on {exc.url}; rotation scheduled",
            )
            # Let the fire-and-forget rotation task finish (or fail) before we
            # unwind, so the new proxy/context is in place on the way out.
            if self._rotate_task is not None:
                with contextlib.suppress(Exception):
                    await self._rotate_task
                self._rotate_task = None
            raise
        if (
            report.succeeded > 0
            and self._cookies_path is not None
            and self._browser_context is not None
        ):
            with contextlib.suppress(Exception):
                self._cookies_path.parent.mkdir(parents=True, exist_ok=True)
                state = await self._browser_context.storage_state()
                self._cookies_path.write_text(
                    json.dumps(state, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                await self.logger.step("cookies", "info", f"saved {self._cookies_path}")
        await self.logger.step(
            "action",
            "info",
            f"behavior {behavior.name} done: "
            f"{report.succeeded}/{report.total} ok, {report.skipped} skipped",
        )
        return next_state(ActorState.ACTION)

    def _on_ban(self, status: int, url: str) -> None:
        """Sync callback invoked by the executor ban listener.

        Schedules proxy rotation on the manager as a fire-and-forget task so
        the running actor task can be cancelled by the rotation's restart.
        Skipped entirely when NO_PROXY is set: local debugging must not ban
        real pool entries or trigger rotation against the local machine IP.
        """
        if self.manager is not None and os.environ.get("NO_PROXY", "").lower() not in ("1", "true"):
            self._rotate_task = asyncio.create_task(
                self.manager.rotate_proxy(self.name),
                name=f"rotate:{self.name}",
            )

    async def _on_extract(self) -> ActorState:
        await self.logger.step("extract", "info", "extract data")
        return next_state(ActorState.EXTRACT)

    async def _on_report(self) -> ActorState:
        await self.logger.step("report", "info", "report generated")
        return next_state(ActorState.REPORT)
