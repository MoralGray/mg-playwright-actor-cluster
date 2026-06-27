from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from db.logger import create_logger
from db.sqlite import init_db, open_connection
from engine.executor import MAX_ATTEMPTS, ActionExecutor, CaptchaPause
from engine.llm_fallback import resolve_captcha
from engine.yaml_reader import Behavior, Step


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0) if self._responses else ""


def _page() -> MagicMock:
    page = MagicMock()
    page.goto = AsyncMock()
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.type = AsyncMock()
    page.focus = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"png")
    page.content = AsyncMock(return_value="<html></html>")
    # Default evaluate returns an empty list so detect_popup() sees no
    # overlays (otherwise dismiss_popup would call the LLM and skew prompt
    # counts). bezier_mouse_move tolerates a non-2-list start position.
    page.evaluate = AsyncMock(return_value=[])
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.mouse = MagicMock()
    page.mouse.wheel = AsyncMock()
    page.mouse.move = AsyncMock()
    loc = MagicMock()
    loc.count = AsyncMock(return_value=1)
    loc.bounding_box = AsyncMock(return_value={"x": 10.0, "y": 20.0, "width": 40.0, "height": 30.0})
    page.locator = MagicMock(return_value=loc)
    return page


def _make_behavior(steps: list[Step]) -> Behavior:
    return Behavior(name="test", steps=steps)


class TestExecutor(unittest.TestCase):
    def _run(self, coro: object) -> object:
        return asyncio.run(coro)

    def _setup_logger(self, tmp: Path):
        async def make():
            await init_db(tmp / "test.db")
            db = await open_connection(tmp / "test.db")
            logger = await create_logger("actor-x", "example.com", db)
            return logger, db

        return self._run(make())

    def test_happy_run_all_steps_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger, db = self._setup_logger(Path(tmp))
            page = _page()
            behavior = _make_behavior(
                [
                    Step(type="navigate", url="https://example.com"),
                    Step(type="wait", selector="body"),
                ]
            )
            executor = ActionExecutor(page, behavior, logger, backoff=(0.0, 0.0, 0.0))
            report = self._run(executor.run())
            self.assertEqual(report.total, 2)
            self.assertEqual(report.succeeded, 2)
            self.assertEqual(report.skipped, 0)
            self.assertFalse(report.failed)
            self._run(db.close())

    def test_selector_miss_triggers_llm_remap_and_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger, db = self._setup_logger(Path(tmp))
            page = _page()
            call_count = {"wait": 0}

            async def flaky_wait(selector, timeout=None):
                call_count["wait"] += 1
                if call_count["wait"] == 1:
                    raise RuntimeError("not found")
                return None

            page.wait_for_selector = AsyncMock(side_effect=flaky_wait)
            llm = FakeLLM(["button#real"])
            behavior = _make_behavior([Step(type="wait", selector="button#missing")])
            executor = ActionExecutor(page, behavior, logger, llm=llm, backoff=(0.0,))
            report = self._run(executor.run())
            self.assertEqual(report.succeeded, 1)
            self.assertEqual(len(llm.prompts), 1)
            self._run(db.close())

    def test_retry_exhaustion_marks_step_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger, db = self._setup_logger(Path(tmp))
            page = _page()
            page.wait_for_selector = AsyncMock(side_effect=RuntimeError("never"))
            behavior = _make_behavior([Step(type="wait", selector="body")])
            executor = ActionExecutor(page, behavior, logger, backoff=(0.0, 0.0, 0.0))
            report = self._run(executor.run())
            self.assertEqual(report.skipped, 1)
            self.assertEqual(report.succeeded, 0)
            self.assertTrue(report.failed)
            outcome = report.outcomes[0]
            self.assertFalse(outcome.ok)
            self.assertEqual(outcome.attempts, MAX_ATTEMPTS)
            self._run(db.close())

    def test_llm_remap_failure_falls_back_to_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger, db = self._setup_logger(Path(tmp))
            page = _page()
            page.wait_for_selector = AsyncMock(side_effect=RuntimeError("never"))
            llm = FakeLLM([""])  # LLM returns nothing usable
            behavior = _make_behavior([Step(type="wait", selector="body")])
            executor = ActionExecutor(page, behavior, logger, llm=llm, backoff=(0.0, 0.0, 0.0))
            report = self._run(executor.run())
            self.assertEqual(report.skipped, 1)
            self._run(db.close())

    def test_captcha_detected_raises_pause(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger, db = self._setup_logger(Path(tmp))
            page = _page()
            page.content = AsyncMock(return_value="<div class='g-recaptcha'></div>")
            executor = ActionExecutor(page, _make_behavior([]), logger, backoff=(0.0,))
            decision = self._run(resolve_captcha(None, page))
            self.assertTrue(decision.is_captcha)
            with self.assertRaises(CaptchaPause):
                self._run(executor.check_captcha())
            self._run(db.close())

    def test_step_error_not_miss_still_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger, db = self._setup_logger(Path(tmp))
            page = _page()
            page.goto = AsyncMock(side_effect=RuntimeError("net"))
            behavior = _make_behavior([Step(type="navigate", url="https://example.com")])
            executor = ActionExecutor(page, behavior, logger, backoff=(0.0, 0.0, 0.0))
            report = self._run(executor.run())
            self.assertEqual(report.skipped, 1)
            self._run(db.close())

    def test_no_llm_selector_miss_goes_straight_to_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger, db = self._setup_logger(Path(tmp))
            page = _page()
            page.wait_for_selector = AsyncMock(side_effect=RuntimeError("nf"))
            behavior = _make_behavior([Step(type="wait", selector="body")])
            executor = ActionExecutor(page, behavior, logger, backoff=(0.0, 0.0, 0.0))
            report = self._run(executor.run())
            self.assertEqual(report.skipped, 1)
            self._run(db.close())

    def test_resolve_mode_selector_skips_llm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger, db = self._setup_logger(Path(tmp))
            page = _page()
            llm = FakeLLM(["button#x"])
            behavior = _make_behavior([Step(type="wait", selector="body")])
            executor = ActionExecutor(page, behavior, logger, llm=llm, backoff=(0.0,))
            self._run(executor.run())
            self.assertEqual(len(llm.prompts), 0)
            page.wait_for_selector.assert_called_with("body", timeout=15000)
            self._run(db.close())

    def test_wait_input_blocks_until_set_input_then_substitutes_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger, db = self._setup_logger(Path(tmp))
            page = _page()
            inputs: dict[str, str] = {}
            event = asyncio.Event()
            behavior = _make_behavior(
                [
                    Step(type="wait_input", prompt="Enter SMS code", name="sms"),
                    Step(type="fill", selector="input[name='code']", value="$ACTOR_CODE"),
                ]
            )
            executor = ActionExecutor(
                page,
                behavior,
                logger,
                backoff=(0.0,),
                inputs=inputs,
                input_event=event,
            )

            async def provide_code_after_wait() -> None:
                await asyncio.sleep(0.05)
                inputs["ACTOR_CODE"] = "12345"
                event.set()

            async def go() -> object:
                task = asyncio.create_task(provide_code_after_wait())
                try:
                    return await executor.run()
                finally:
                    task.cancel()

            report = self._run(go())
            self.assertEqual(report.succeeded, 2)
            # The fill step must have been called with the substituted code.
            # human_type internally calls page.fill; assert the value reached.
            self.assertEqual(executor.behavior.steps[1].value, "12345")
            self._run(db.close())

    def test_extract_table_deterministic_returns_json_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger, db = self._setup_logger(Path(tmp))
            page = _page()
            page.evaluate = AsyncMock(return_value=[[["Search query", "Count"], ["носки", "100"]]])
            behavior = _make_behavior([Step(type="extract_table", name="t")])
            executor = ActionExecutor(page, behavior, logger, backoff=(0.0,))
            report = self._run(executor.run())
            self.assertEqual(report.succeeded, 1)
            import json

            rows = json.loads(report.outcomes[0].message)
            self.assertEqual(rows[0][1], ["носки", "100"])
            self._run(db.close())

    def test_extract_table_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger, db = self._setup_logger(Path(tmp))
            page = _page()
            page.evaluate = AsyncMock(return_value='[["A"]]')
            behavior = _make_behavior([Step(type="extract_table", name="t")])
            executor = ActionExecutor(page, behavior, logger, backoff=(0.0,))
            report = self._run(executor.run())
            self.assertEqual(report.succeeded, 1)
            self._run(db.close())


if __name__ == "__main__":
    unittest.main()
