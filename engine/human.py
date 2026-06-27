from __future__ import annotations

import math
import random
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

RNG = random.Random

DELAY_MIN_MS = 200.0
DELAY_MAX_MS = 1500.0
TYPE_MIN_MS = 40.0
TYPE_MAX_MS = 250.0
JITTER_MAX_PX = 3


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def human_delay(rng: RNG | None = None) -> float:
    """Log-normal inter-action delay clamped to [DELAY_MIN_MS, DELAY_MAX_MS] ms."""
    r = rng or random
    mu = math.log(500.0)
    sigma = 0.5
    val = r.lognormvariate(mu, sigma)
    return _clamp(val, DELAY_MIN_MS, DELAY_MAX_MS)


def type_char_delay(rng: RNG | None = None) -> float:
    """Per-keystroke delay clamped to [TYPE_MIN_MS, TYPE_MAX_MS] ms."""
    r = rng or random
    mu = math.log(90.0)
    sigma = 0.4
    val = r.lognormvariate(mu, sigma)
    return _clamp(val, TYPE_MIN_MS, TYPE_MAX_MS)


def jitter_coords(x: float, y: float, rng: RNG | None = None) -> tuple[float, float]:
    """Apply small +/- JITTER_MAX_PX offset to a coordinate pair."""
    r = rng or random
    dx = r.uniform(-JITTER_MAX_PX, JITTER_MAX_PX)
    dy = r.uniform(-JITTER_MAX_PX, JITTER_MAX_PX)
    return x + dx, y + dy


def _cubic_bezier(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    u = 1.0 - t
    x = u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0]
    y = u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]
    return x, y


def bezier_path(
    start: tuple[float, float],
    end: tuple[float, float],
    rng: RNG | None = None,
    steps: int = 12,
    tremor: float = 1.5,
) -> list[tuple[float, float]]:
    """Sample points along a cubic bezier curve between start and end.

    A small per-point sub-pixel tremor is added to intermediate samples so
    the resulting movementX/Y distribution is non-uniform like a real hand;
    the start and end points are kept exact so click targets stay accurate.
    """
    r = rng or random
    sx, sy = start
    ex, ey = end
    midx = (sx + ex) / 2.0
    midy = (sy + ey) / 2.0
    spread = math.hypot(ex - sx, ey - sy) * 0.4
    c1 = (midx + r.uniform(-spread, spread), midy + r.uniform(-spread, spread))
    c2 = (midx + r.uniform(-spread, spread), midy + r.uniform(-spread, spread))
    pts: list[tuple[float, float]] = []
    n = max(steps - 1, 1)
    for i in range(steps):
        x, y = _cubic_bezier(start, c1, c2, end, i / n)
        # tremor the intermediate points only; endpoints stay exact
        if i != 0 and i != steps - 1:
            x += r.uniform(-tremor, tremor)
            y += r.uniform(-tremor, tremor)
        pts.append((x, y))
    return pts


async def bezier_mouse_move(
    page: Page,
    x: float,
    y: float,
    rng: RNG | None = None,
    steps: int = 12,
) -> None:
    """Move the mouse to (x, y) along a Bezier curve with human-like jitter."""
    r = rng or random
    start = await page.evaluate("() => [window.__lastMouseX ?? 0, window.__lastMouseY ?? 0]")
    if not isinstance(start, list) or len(start) != 2:
        start = [0.0, 0.0]
    for point in bezier_path(
        (float(start[0]), float(start[1])),
        jitter_coords(x, y, r),
        rng=r,
        steps=steps,
    ):
        await page.mouse.move(point[0], point[1], steps=1)
    await page.mouse.move(x, y, steps=1)


def with_human_delay(
    coro_factory: Callable[[], object],
    rng: RNG | None = None,
) -> object:
    """Return an awaitable that sleeps a human delay then runs coro_factory().

    Helper for composing deterministic waits around async work.
    """
    import asyncio

    async def _wrapped() -> object:
        await asyncio.sleep(human_delay(rng) / 1000.0)
        return await coro_factory()

    return _wrapped()


async def human_type(
    page: Page,
    selector: str,
    text: str,
    rng: RNG | None = None,
) -> None:
    """Type `text` into `selector` one character at a time with variable delay."""
    import asyncio

    await page.focus(selector)
    await page.fill(selector, "")
    for ch in text:
        await page.type(selector, ch, delay=0)
        await asyncio.sleep(type_char_delay(rng) / 1000.0)
