from __future__ import annotations

import asyncio
import itertools
import random
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from engine.actions import (
    SelectorMiss,
    StepError,
    do_click,
    do_fill,
    do_navigate,
    do_press,
    do_screenshot,
    do_scroll,
    do_wait,
    execute_step,
)
from engine.human import (
    bezier_path,
    human_delay,
    jitter_coords,
    type_char_delay,
)
from engine.yaml_reader import Step


def _page() -> MagicMock:
    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.type = AsyncMock()
    page.focus = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.mouse = MagicMock()
    page.mouse.wheel = AsyncMock()
    page.mouse.move = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"png")
    page.content = AsyncMock(return_value="<html></html>")
    page.evaluate = AsyncMock(return_value=[0.0, 0.0])
    loc = MagicMock()
    loc.count = AsyncMock(return_value=1)
    loc.bounding_box = AsyncMock(return_value={"x": 10.0, "y": 20.0, "width": 40.0, "height": 30.0})
    page.locator = MagicMock(return_value=loc)
    return page


class TestHumanHelpers(unittest.TestCase):
    def test_human_delay_within_bounds(self) -> None:
        for _ in range(500):
            d = human_delay(random.Random())
            self.assertGreaterEqual(d, 200.0)
            self.assertLessEqual(d, 1500.0)

    def test_human_delay_deterministic_with_seed(self) -> None:
        a = human_delay(random.Random(7))
        b = human_delay(random.Random(7))
        self.assertEqual(a, b)

    def test_type_char_delay_within_bounds(self) -> None:
        for _ in range(500):
            d = type_char_delay(random.Random())
            self.assertGreaterEqual(d, 40.0)
            self.assertLessEqual(d, 250.0)

    def test_jitter_bounds(self) -> None:
        for _ in range(200):
            x, y = jitter_coords(100.0, 200.0, random.Random())
            self.assertLessEqual(abs(x - 100.0), 3.0)
            self.assertLessEqual(abs(y - 200.0), 3.0)

    def test_bezier_endpoints_match(self) -> None:
        path = bezier_path((0.0, 0.0), (100.0, 50.0), random.Random(1), steps=12)
        self.assertEqual(len(path), 12)
        self.assertAlmostEqual(path[-1][0], 100.0, delta=3.0)
        self.assertAlmostEqual(path[-1][1], 50.0, delta=3.0)

    def test_bezier_interpolates_monotonic_x(self) -> None:
        path = bezier_path((0.0, 0.0), (100.0, 0.0), random.Random(0), steps=10)
        xs = [p[0] for p in path]
        for a, b in itertools.pairwise(xs):
            self.assertLessEqual(a, b + 5.0)


class TestStepHandlers(unittest.TestCase):
    def _run(self, coro: object) -> object:
        return asyncio.run(coro)

    def test_navigate_success(self) -> None:
        page = _page()
        step = Step(type="navigate", url="https://example.com")
        result = self._run(do_navigate(page, step))
        self.assertTrue(result.ok)
        page.goto.assert_awaited_once()

    def test_navigate_failure_raises_step_error(self) -> None:
        page = _page()
        page.goto = AsyncMock(side_effect=RuntimeError("boom"))
        step = Step(type="navigate", url="https://example.com")
        with self.assertRaises(StepError):
            self._run(do_navigate(page, step))

    def test_wait_selector_miss_raises(self) -> None:
        page = _page()
        page.wait_for_selector = AsyncMock(side_effect=RuntimeError("not found"))
        step = Step(type="wait", selector="body")
        with self.assertRaises(SelectorMiss):
            self._run(do_wait(page, step))

    def test_click_success_uses_bezier(self) -> None:
        page = _page()
        step = Step(type="click", selector="button#go")
        result = self._run(do_click(page, step, rng=random.Random(1)))
        self.assertTrue(result.ok)
        self.assertEqual(result.selector, "button#go")
        self.assertGreater(page.mouse.move.await_count, 1)
        page.click.assert_awaited_once()

    def test_click_missing_selector_raises_miss(self) -> None:
        page = _page()
        page.wait_for_selector = AsyncMock(side_effect=RuntimeError("nf"))
        step = Step(type="click", selector="button#go")
        with self.assertRaises(SelectorMiss):
            self._run(do_click(page, step, rng=random.Random(1)))

    def test_fill_types_each_char(self) -> None:
        page = _page()
        step = Step(type="fill", selector="input", value="abcd")
        result = self._run(do_fill(page, step, rng=random.Random(1)))
        self.assertTrue(result.ok)
        self.assertEqual(page.type.await_count, 4)
        page.focus.assert_awaited_once()

    def test_fill_missing_selector_raises_miss(self) -> None:
        page = _page()
        page.wait_for_selector = AsyncMock(side_effect=RuntimeError("nf"))
        step = Step(type="fill", selector="input", value="x")
        with self.assertRaises(SelectorMiss):
            self._run(do_fill(page, step, rng=random.Random(1)))

    def test_press_calls_keyboard(self) -> None:
        page = _page()
        step = Step(type="press", value="Enter")
        result = self._run(do_press(page, step))
        self.assertTrue(result.ok)
        page.keyboard.press.assert_awaited_once_with("Enter")

    def test_scroll_with_numeric_value(self) -> None:
        page = _page()
        step = Step(type="scroll", value="600")
        result = self._run(do_scroll(page, step, rng=random.Random(1)))
        self.assertTrue(result.ok)
        page.mouse.wheel.assert_awaited_once()

    def test_scroll_default_when_no_value(self) -> None:
        page = _page()
        step = Step(type="scroll")
        result = self._run(do_scroll(page, step, rng=random.Random(1)))
        self.assertTrue(result.ok)
        page.mouse.wheel.assert_awaited_once()

    def test_screenshot_writes_path(self) -> None:
        page = _page()
        step = Step(type="screenshot", name="landing")
        result = self._run(do_screenshot(page, step))
        self.assertTrue(result.ok)
        page.screenshot.assert_awaited_once()

    def test_execute_step_dispatch(self) -> None:
        page = _page()
        step = Step(type="navigate", url="https://example.com")
        result = self._run(execute_step(page, step, rng=random.Random(1)))
        self.assertTrue(result.ok)

    def test_execute_step_unknown_type_raises(self) -> None:
        page = _page()
        step = Step(type="bogus", selector="a")
        with self.assertRaises(StepError):
            self._run(execute_step(page, step, rng=random.Random(1)))

    def test_execute_step_dispatches_all_known_types(self) -> None:
        known = {"navigate", "wait", "click", "fill", "press", "scroll", "screenshot"}
        for t in known:
            page = _page()
            if t == "navigate":
                step = Step(type=t, url="https://example.com")
            elif t == "fill":
                step = Step(type=t, selector="input", value="x")
            elif t == "press":
                step = Step(type=t, value="Enter")
            else:
                step = Step(type=t, selector="a", value="600")
            result = self._run(execute_step(page, step, rng=random.Random(1)))
            self.assertTrue(result.ok, msg=f"{t} failed")


if __name__ == "__main__":
    unittest.main()
