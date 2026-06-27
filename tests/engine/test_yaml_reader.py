from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from engine.yaml_reader import (
    Behavior,
    Step,
    apply_variables,
    build_var_map,
    load_behavior,
    load_behavior_or_none,
)

BEHAVIOR_DIR = Path(__file__).resolve().parent.parent.parent / "configs" / "behavior"


def _write(tmp: Path, name: str, content: str) -> Path:
    p = tmp / name
    p.write_text(content, encoding="utf-8")
    return p


class TestYamlReader(unittest.TestCase):
    def test_load_wildberries_behavior(self) -> None:
        behavior = load_behavior(BEHAVIOR_DIR / "wildberries.yaml")
        self.assertIsInstance(behavior, Behavior)
        self.assertEqual(behavior.name, "wildberries")
        self.assertGreater(len(behavior.steps), 5)
        self.assertTrue(all(isinstance(s, Step) for s in behavior.steps))
        types = {s.type for s in behavior.steps}
        self.assertIn("navigate", types)
        self.assertIn("fill", types)
        self.assertIn("click", types)

    def test_step_fields_populated(self) -> None:
        behavior = load_behavior(BEHAVIOR_DIR / "wildberries.yaml")
        nav = next(s for s in behavior.steps if s.type == "navigate")
        self.assertIsNotNone(nav.url)
        fill = next(s for s in behavior.steps if s.type == "fill")
        self.assertIsNotNone(fill.selector)
        self.assertIsNotNone(fill.value)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_behavior("/nonexistent/behavior.yaml")

    def test_missing_file_returns_none_with_helper(self) -> None:
        self.assertIsNone(load_behavior_or_none("/nonexistent/behavior.yaml"))

    def test_malformed_yaml_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(Path(tmp), "bad.yaml", "name: : :\n  steps: [")
            with self.assertRaises(ValueError):
                load_behavior(p)

    def test_missing_top_keys_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(Path(tmp), "no_steps.yaml", "name: foo\n")
            with self.assertRaises(ValueError):
                load_behavior(p)

    def test_missing_step_type_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(
                Path(tmp),
                "bad_step.yaml",
                "name: foo\nsteps:\n  - selector: a\n",
            )
            with self.assertRaises(ValueError):
                load_behavior(p)

    def test_unknown_step_type_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(
                Path(tmp),
                "unknown.yaml",
                "name: foo\nsteps:\n  - type: teleport\n",
            )
            with self.assertRaises(ValueError):
                load_behavior(p)

    def test_step_requires_selector(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(
                Path(tmp),
                "no_sel.yaml",
                "name: foo\nsteps:\n  - type: click\n",
            )
            with self.assertRaises(ValueError):
                load_behavior(p)

    def test_navigate_requires_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(
                Path(tmp),
                "no_url.yaml",
                "name: foo\nsteps:\n  - type: navigate\n",
            )
            with self.assertRaises(ValueError):
                load_behavior(p)

    def test_fill_requires_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(
                Path(tmp),
                "no_val.yaml",
                "name: foo\nsteps:\n  - type: fill\n    selector: a\n",
            )
            with self.assertRaises(ValueError):
                load_behavior(p)

    def test_root_not_mapping_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(Path(tmp), "list.yaml", "- one\n- two\n")
            with self.assertRaises(ValueError):
                load_behavior(p)

    def test_steps_not_list_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(
                Path(tmp),
                "scalar_steps.yaml",
                "name: foo\nsteps: notalist\n",
            )
            with self.assertRaises(ValueError):
                load_behavior(p)

    def test_wait_input_requires_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(
                Path(tmp),
                "no_prompt.yaml",
                "name: foo\nsteps:\n  - type: wait_input\n",
            )
            with self.assertRaises(ValueError):
                load_behavior(p)

    def test_wait_input_valid_with_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(
                Path(tmp),
                "wait.yaml",
                "name: foo\nsteps:\n  - type: wait_input\n    prompt: Enter SMS\n",
            )
            behavior = load_behavior(p)
            step = behavior.steps[0]
            self.assertEqual(step.type, "wait_input")
            self.assertEqual(step.prompt, "Enter SMS")
            self.assertIsNone(step.selector)

    def test_extract_table_valid_without_selector(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = _write(
                Path(tmp),
                "extract.yaml",
                "name: foo\nsteps:\n  - type: extract_table\n    name: t\n",
            )
            behavior = load_behavior(p)
            self.assertEqual(behavior.steps[0].type, "extract_table")

    def test_build_var_map_merges_extra_vars(self) -> None:
        class FakeCreds:
            login = "+70000000000"
            password = ""

        class FakeProfile:
            name = "wb-det"
            credentials = FakeCreds()

        var_map = build_var_map(FakeProfile, extra_vars={"ACTOR_CODE": "12345"})
        self.assertEqual(var_map["ACTOR_PHONE"], "+70000000000")
        self.assertEqual(var_map["ACTOR_CODE"], "12345")

    def test_apply_variables_substitutes_actor_code(self) -> None:
        behavior = Behavior(
            name="t",
            steps=[
                Step(type="wait_input", prompt="Enter SMS"),
                Step(type="fill", selector="input[name='code']", value="$ACTOR_CODE"),
            ],
        )
        var_map = {"ACTOR_CODE": "9999"}
        out = apply_variables(behavior, var_map)
        self.assertEqual(out.steps[1].value, "9999")
        # The wait_input step is untouched (no $VARIABLE in it).
        self.assertEqual(out.steps[0].prompt, "Enter SMS")


if __name__ == "__main__":
    unittest.main()
