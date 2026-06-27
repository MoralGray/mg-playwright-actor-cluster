from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from engine.human import bezier_mouse_move, human_type, jitter_coords
from engine.yaml_reader import Step

if TYPE_CHECKING:
    from playwright.async_api import Page

DEFAULT_TIMEOUT_MS = 15_000


class StepError(Exception):
    """Raised when a deterministic step fails (e.g. selector missing)."""


class SelectorMiss(StepError):
    """Raised when a selector cannot be resolved — triggers LLM remap."""


@dataclass(frozen=True, slots=True)
class StepResult:
    ok: bool
    step_type: str
    message: str
    selector: str | None = None


async def do_navigate(page: Page, step: Step) -> StepResult:
    assert step.url is not None
    try:
        await page.goto(step.url, timeout=step.timeout_ms or DEFAULT_TIMEOUT_MS)
    except Exception as exc:
        raise StepError(f"navigate to {step.url} failed: {exc}") from exc
    return StepResult(ok=True, step_type="navigate", message=f"navigated to {step.url}")


async def do_wait(page: Page, step: Step) -> StepResult:
    assert step.selector is not None
    timeout = step.timeout_ms or DEFAULT_TIMEOUT_MS
    try:
        await page.wait_for_selector(step.selector, timeout=timeout)
    except Exception as exc:
        raise SelectorMiss(f"wait selector {step.selector!r} not found: {exc}") from exc
    return StepResult(ok=True, step_type="wait", message=f"waited for {step.selector}")


async def do_click(page: Page, step: Step, rng: random.Random | None = None) -> StepResult:
    assert step.selector is not None
    timeout = step.timeout_ms or DEFAULT_TIMEOUT_MS
    try:
        await page.wait_for_selector(step.selector, timeout=timeout)
    except Exception as exc:
        raise SelectorMiss(f"click selector {step.selector!r} not found: {exc}") from exc
    box = await page.locator(step.selector).bounding_box()
    if box is not None:
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        await bezier_mouse_move(page, cx, cy, rng=rng)
    try:
        await page.click(step.selector, timeout=timeout)
    except Exception as exc:
        raise StepError(f"click {step.selector!r} failed: {exc}") from exc
    return StepResult(
        ok=True, step_type="click", message=f"clicked {step.selector}", selector=step.selector
    )


async def do_fill(page: Page, step: Step, rng: random.Random | None = None) -> StepResult:
    assert step.selector is not None
    assert step.value is not None
    timeout = step.timeout_ms or DEFAULT_TIMEOUT_MS
    try:
        await page.wait_for_selector(step.selector, timeout=timeout)
    except Exception as exc:
        raise SelectorMiss(f"fill selector {step.selector!r} not found: {exc}") from exc
    try:
        count = await page.locator(step.selector).count()
        if count > 1:
            for i in range(min(count, len(step.value))):
                await page.locator(step.selector).nth(i).fill(step.value[i])
        else:
            await human_type(page, step.selector, step.value, rng=rng)
    except Exception as exc:
        raise StepError(f"fill {step.selector!r} failed: {exc}") from exc
    return StepResult(
        ok=True,
        step_type="fill",
        message=f"filled {step.selector} with {len(step.value)} chars",
        selector=step.selector,
    )


async def do_press(page: Page, step: Step) -> StepResult:
    assert step.value is not None
    try:
        await page.keyboard.press(step.value)
    except Exception as exc:
        raise StepError(f"press {step.value!r} failed: {exc}") from exc
    return StepResult(ok=True, step_type="press", message=f"pressed {step.value}")


async def do_scroll(page: Page, step: Step, rng: random.Random | None = None) -> StepResult:
    # Accept negative values (scroll up): "-300" or "300". A bare "-" or
    # non-numeric value falls back to the default 600.
    raw = step.value.strip() if step.value else ""
    dy = int(raw) if raw and raw.lstrip("-").isdigit() else 600
    dx = 0
    if rng is not None:
        dy += rng.randint(-50, 50)
    try:
        await page.mouse.wheel(dx, dy)
    except Exception as exc:
        raise StepError(f"scroll failed: {exc}") from exc
    return StepResult(ok=True, step_type="scroll", message=f"scrolled dy={dy}")


async def do_screenshot(page: Page, step: Step) -> StepResult:
    name = step.name or "screenshot"
    path = f"output/{name}.png"
    try:
        await page.screenshot(path=path)
    except Exception as exc:
        raise StepError(f"screenshot failed: {exc}") from exc
    return StepResult(ok=True, step_type="screenshot", message=f"captured {path}")


async def execute_step(
    page: Page,
    step: Step,
    rng: random.Random | None = None,
) -> StepResult:
    """Dispatch a single Step to its deterministic handler."""
    handler = {
        "navigate": do_navigate,
        "wait": do_wait,
        "click": do_click,
        "fill": do_fill,
        "press": do_press,
        "scroll": do_scroll,
        "screenshot": do_screenshot,
    }.get(step.type)
    if handler is None:
        raise StepError(f"no handler for step type {step.type!r}")
    if handler in (do_click, do_fill, do_scroll):
        return await handler(page, step, rng=rng)
    return await handler(page, step)


__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "SelectorMiss",
    "StepError",
    "StepResult",
    "do_click",
    "do_fill",
    "do_navigate",
    "do_press",
    "do_screenshot",
    "do_scroll",
    "do_wait",
    "execute_step",
    "jitter_coords",
]
