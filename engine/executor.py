from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from db.logger import Logger
from engine.actions import StepError, execute_step
from engine.ban import BanDetected, install_ban_listener
from engine.llm_fallback import (
    LLMClient,
    dismiss_popup,
    remap_selector,
    resolve_captcha,
)
from engine.yaml_reader import Behavior, Step

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

BACKOFF_SECONDS: tuple[float, ...] = (1.0, 3.0, 9.0)
MAX_ATTEMPTS = 3


class CaptchaPause(Exception):
    """Raised when a captcha is detected and the executor must pause the task."""


@dataclass(frozen=True, slots=True)
class StepOutcome:
    step_index: int
    step_type: str
    ok: bool
    attempts: int
    message: str
    selector: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    behavior_name: str
    total: int
    succeeded: int
    skipped: int
    outcomes: list[StepOutcome] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.skipped > 0


class ActionExecutor:
    """Iterates a Behavior's steps, dispatches to deterministic handlers,
    retries with exponential backoff, and falls back to the LLM when a
    selector miss is detected. Captcha pauses the task.
    """

    def __init__(
        self,
        page: Page,
        behavior: Behavior,
        logger: Logger,
        llm: LLMClient | None = None,
        rng: random.Random | None = None,
        backoff: tuple[float, ...] = BACKOFF_SECONDS,
        predefined_dismiss: tuple[str, ...] = (),
        on_ban: Callable[[int, str], None] | None = None,
        inputs: dict[str, str] | None = None,
        input_event: asyncio.Event | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        self.page = page
        self.behavior = behavior
        self.logger = logger
        self.llm = llm
        self.rng = rng or random.Random()
        self.backoff = backoff
        self.predefined_dismiss = predefined_dismiss
        self.on_ban = on_ban
        self._inputs = inputs if inputs is not None else {}
        self._input_event = input_event
        self._stop_event = stop_event
        self._ban_event = asyncio.Event()
        self._ban_status = 0
        self._ban_url = ""
        self._ban_listener: Callable[object, None] | None = None

    async def run(self) -> ExecutionReport:
        if self.on_ban is not None:
            self._ban_listener = install_ban_listener(self.page, self._handle_ban)
        outcomes: list[StepOutcome] = []
        succeeded = 0
        skipped = 0
        # Use while + index instead of for/enumerate so that _apply_extra_vars
        # (called from wait_input) can replace self.behavior.steps mid-run and
        # subsequent iterations pick up the new behavior steps with substituted
        # $ACTOR_CODE rather than the original steps captured by enumerate().
        index = 0
        while index < len(self.behavior.steps):
            step = self.behavior.steps[index]
            if self._ban_event.is_set():
                await self.logger.step("ban", "error", "ban response detected; halting run")
                raise BanDetected(self._ban_status, self._ban_url)
            # Per epic-error-recovery spec: before each step attempt, check
            # for captcha (pauses task) and dismiss unexpected popups.
            await self.check_captcha()
            await self.handle_popup()
            outcome = await self._run_step(index, step)
            outcomes.append(outcome)
            if outcome.ok:
                succeeded += 1
            else:
                skipped += 1
            index += 1
        return ExecutionReport(
            behavior_name=self.behavior.name,
            total=len(self.behavior.steps),
            succeeded=succeeded,
            skipped=skipped,
            outcomes=outcomes,
        )

    def _handle_ban(self, status: int, url: str) -> None:
        self._ban_status = status
        self._ban_url = url
        self._ban_event.set()
        if self.on_ban is not None:
            try:
                self.on_ban(status, url)
            except Exception:
                logger.exception("on_ban callback raised")

    async def _run_step(self, index: int, step: Step) -> StepOutcome:
        if step.type == "screenshot":
            return await self._run_screenshot(index, step)
        if step.type == "wait_input":
            return await self._run_wait_input(index, step)
        if step.type == "extract_table":
            return await self._run_extract_table(index, step)
        last_message = ""
        selector_used = step.selector
        current_selector = step.selector
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                result = await execute_step(self.page, step, rng=self.rng)
                await self.logger.step(
                    f"step[{index}].{step.type}",
                    "info",
                    result.message,
                )
                return StepOutcome(
                    step_index=index,
                    step_type=step.type,
                    ok=True,
                    attempts=attempt,
                    message=result.message,
                    selector=selector_used,
                )
            except CaptchaPause:
                raise
            except StepError as exc:
                last_message = repr(exc)
                remapped = await self._maybe_remap(step, current_selector)
                if remapped is not None:
                    current_selector = remapped
                    step = _with_selector(step, remapped)
                    selector_used = remapped
                    await self.logger.step(
                        f"step[{index}].{step.type}",
                        "warn",
                        f"LLM remap selector -> {remapped}",
                    )
                    continue
                if attempt < MAX_ATTEMPTS:
                    delay = self.backoff[min(attempt - 1, len(self.backoff) - 1)]
                    await self.logger.step(
                        f"step[{index}].{step.type}",
                        "warn",
                        f"attempt {attempt} failed: {last_message}; retry in {delay}s",
                    )
                    await asyncio.sleep(delay)
                    continue
                await self._capture_failure(step, index, last_message)
                await self.logger.step(
                    f"step[{index}].{step.type}",
                    "error",
                    f"step failed after {attempt} attempts: {last_message}",
                )

        return StepOutcome(
            step_index=index,
            step_type=step.type,
            ok=False,
            attempts=MAX_ATTEMPTS,
            message=last_message,
            selector=selector_used,
        )

    async def _run_screenshot(self, index: int, step: Step) -> StepOutcome:
        name = step.name or "screenshot"
        try:
            # Use the session-scoped Logger path so concurrent actors do not
            # clobber each other's output/{name}.png files.
            path = await self.logger.screenshot(self.page, name)
            message = f"captured {path}"
            await self.logger.step(f"step[{index}].screenshot", "info", message, screenshot=path)
            return StepOutcome(
                step_index=index,
                step_type="screenshot",
                ok=True,
                attempts=1,
                message=message,
            )
        except Exception as exc:
            await self.logger.step(
                f"step[{index}].screenshot", "error", f"screenshot failed: {exc!r}"
            )
            return StepOutcome(
                step_index=index,
                step_type="screenshot",
                ok=False,
                attempts=1,
                message=repr(exc),
            )

    async def _run_wait_input(self, index: int, step: Step) -> StepOutcome:
        """Block until an out-of-band input (e.g. SMS code) is provided via
        ``Actor.set_input``. The provided value is stored in the var map so
        subsequent steps that reference ``$ACTOR_CODE`` are substituted.
        """
        prompt = step.prompt or step.name or "waiting for input"
        await self.logger.step(
            f"step[{index}].wait_input",
            "info",
            f"waiting for input: {prompt}",
        )
        if self._input_event is None:
            await self.logger.step(
                f"step[{index}].wait_input",
                "error",
                "no input_event wired; cannot wait",
            )
            return StepOutcome(
                step_index=index,
                step_type="wait_input",
                ok=False,
                attempts=1,
                message="no input_event wired",
            )
        # Wait for either an operator-provided input or a stop signal so the
        # actor can be terminated cleanly while waiting for an SMS code instead
        # of blocking forever.
        wait_set: set[asyncio.Future[object]] = {asyncio.ensure_future(self._input_event.wait())}
        if self._stop_event is not None:
            wait_set.add(asyncio.ensure_future(self._stop_event.wait()))
        try:
            await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for fut in wait_set:
                if not fut.done():
                    fut.cancel()
        if self._stop_event is not None and self._stop_event.is_set():
            await self.logger.step(
                f"step[{index}].wait_input",
                "warn",
                "stop requested while waiting for input; aborting step",
            )
            return StepOutcome(
                step_index=index,
                step_type="wait_input",
                ok=False,
                attempts=1,
                message="stopped while waiting for input",
            )
        code = self._inputs.get("ACTOR_CODE", "")
        # Re-substitute remaining steps so $ACTOR_CODE resolves to the code
        # the operator supplied for the rest of this behavior run.
        self._apply_extra_vars({"ACTOR_CODE": code}, from_index=index + 1)
        await self.logger.step(
            f"step[{index}].wait_input",
            "info",
            f"received ACTOR_CODE (len={len(code)})",
        )
        return StepOutcome(
            step_index=index,
            step_type="wait_input",
            ok=True,
            attempts=1,
            message=f"received input for: {prompt}",
        )

    def _apply_extra_vars(self, extra_vars: dict[str, str], from_index: int) -> None:
        """Re-substitute variables in steps at index >= from_index.

        Mutates ``self.behavior`` in place by rebuilding the step list with
        the merged var map applied. Used after ``wait_input`` to propagate
        the operator-provided SMS code into later ``$ACTOR_CODE`` steps.
        """
        from engine.yaml_reader import substitute_variables

        merged: list[Step] = []
        for i, s in enumerate(self.behavior.steps):
            if i < from_index:
                merged.append(s)
                continue
            merged.append(
                Step(
                    type=s.type,
                    selector=substitute_variables(s.selector, extra_vars),
                    value=substitute_variables(s.value, extra_vars),
                    url=substitute_variables(s.url, extra_vars),
                    name=substitute_variables(s.name, extra_vars),
                    timeout_ms=s.timeout_ms,
                    prompt=substitute_variables(s.prompt, extra_vars),
                )
            )
        self.behavior = Behavior(
            name=self.behavior.name,
            steps=merged,
            raw=self.behavior.raw,
        )

    async def _run_extract_table(self, index: int, step: Step) -> StepOutcome:
        """Extract a table from the page as JSON rows.

        Runs a JS snippet that collects every ``<table>`` (scoped to
        ``step.selector`` if given) and returns rows of cell text.
        """
        try:
            rows_json = await self._extract_table_deterministic(step)
            await self.logger.step(
                f"step[{index}].extract_table",
                "info",
                rows_json,
            )
            return StepOutcome(
                step_index=index,
                step_type="extract_table",
                ok=True,
                attempts=1,
                message=rows_json,
            )
        except Exception as exc:
            await self.logger.step(
                f"step[{index}].extract_table",
                "error",
                f"extract_table failed: {exc!r}",
            )
            return StepOutcome(
                step_index=index,
                step_type="extract_table",
                ok=False,
                attempts=1,
                message=repr(exc),
            )

    async def _extract_table_deterministic(self, step: Step) -> str:
        import json

        scope = step.selector or "table"
        js = """(scope) => {
            const root = scope && scope !== 'table'
                ? document.querySelector(scope) || document
                : document;
            const tables = Array.from(root.querySelectorAll('table'));
            const out = tables.map((t) => {
                const rows = Array.from(t.querySelectorAll('tr'));
                return rows.map((tr) =>
                    Array.from(tr.querySelectorAll('th,td')).map((c) => (c.innerText || '').trim())
                );
            });
            return out;
        }"""
        # Poll for data cells: React may render the table skeleton before
        # API data populates the cells.  Retry up to 30 times (60s total)
        # with 2s delay until at least one non-empty cell is found.
        for _ in range(30):
            tables = await self.page.evaluate(js, scope)
            rows = tables[0] if tables else []
            has_data = any(cell.strip() for row in rows[1:] for cell in row)
            if has_data:
                return json.dumps(tables, ensure_ascii=False)
            await asyncio.sleep(2)
        return json.dumps(tables, ensure_ascii=False)

    async def _maybe_remap(self, step: Step, current_selector: str | None) -> str | None:
        if self.llm is None or current_selector is None:
            return None
        intent = f"{step.type} target via {current_selector}"
        try:
            return await remap_selector(self.llm, self.page, intent, current_selector)
        except Exception as exc:
            logger.warning("LLM remap failed: %s", exc)
            return None

    async def _capture_failure(self, step: Step, index: int, message: str) -> None:
        try:
            path = await self.logger.screenshot(self.page, f"fail-step-{index}-{step.type}")
            await self.logger.step(
                f"step[{index}].{step.type}",
                "error",
                f"{message}; screenshot={path}",
                screenshot=path,
            )
        except Exception as exc:
            logger.warning("failure screenshot capture failed: %s", exc)

    async def check_captcha(self) -> bool:
        decision = await resolve_captcha(self.llm, self.page)
        if decision.is_captcha:
            await self.logger.step(
                "captcha",
                "warn",
                "captcha detected; pausing task and notifying operator",
            )
            raise CaptchaPause("captcha detected; manual solve required")
        return False

    async def handle_popup(self) -> bool:
        # Try predefined dismiss selectors unconditionally first — they are
        # cheap and do not require an LLM client. Only invoke the LLM vision
        # fallback when a client is configured and predefined dismiss failed.
        try:
            sel = await dismiss_popup(self.llm, self.page, self.predefined_dismiss)
        except Exception as exc:
            logger.warning("popup dismiss failed: %s", exc)
            return False
        if sel is not None:
            await self.logger.step("popup", "info", f"dismissed popup via {sel}")
            return True
        return False


def _strip_json_fence(text: str) -> str:
    """Strip markdown fences and surrounding prose from an LLM JSON reply."""
    import re

    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return text.strip()


def _with_selector(step: Step, selector: str) -> Step:
    return Step(
        type=step.type,
        selector=selector,
        value=step.value,
        url=step.url,
        name=step.name,
        timeout_ms=step.timeout_ms,
        prompt=step.prompt,
    )


__all__ = [
    "BACKOFF_SECONDS",
    "MAX_ATTEMPTS",
    "ActionExecutor",
    "CaptchaPause",
    "ExecutionReport",
    "StepOutcome",
]
