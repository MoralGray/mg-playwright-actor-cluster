from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REQUIRED_TOP = ("name", "steps")
VALID_TYPES = frozenset(
    {
        "navigate",
        "click",
        "fill",
        "wait",
        "scroll",
        "screenshot",
        "press",
        "wait_input",
        "extract_table",
    }
)
REQUIRES_SELECTOR = frozenset({"click", "fill", "wait", "press"})
REQUIRES_URL = frozenset({"navigate"})
REQUIRES_VALUE = frozenset({"fill"})
REQUIRES_PROMPT = frozenset({"wait_input"})


@dataclass(frozen=True, slots=True)
class Step:
    type: str
    selector: str | None = None
    value: str | None = None
    url: str | None = None
    name: str | None = None
    timeout_ms: int | None = None
    prompt: str | None = None


@dataclass(frozen=True, slots=True)
class Behavior:
    name: str
    steps: list[Step] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


def _build_step(raw: dict[str, Any], index: int) -> Step:
    if not isinstance(raw, dict):
        raise ValueError(f"step {index} must be a mapping, got {type(raw).__name__}")
    step_type = raw.get("type")
    if not step_type:
        raise ValueError(f"step {index} missing required field: type")
    if step_type not in VALID_TYPES:
        raise ValueError(
            f"step {index} has unknown type {step_type!r}; valid: {sorted(VALID_TYPES)}"
        )
    if step_type in REQUIRES_SELECTOR and not raw.get("selector"):
        raise ValueError(f"step {index} ({step_type}) requires a selector")
    if step_type in REQUIRES_URL and not raw.get("url"):
        raise ValueError(f"step {index} ({step_type}) requires a url")
    if step_type in REQUIRES_VALUE and raw.get("value") is None:
        raise ValueError(f"step {index} ({step_type}) requires a value")
    if step_type in REQUIRES_PROMPT and not raw.get("prompt"):
        raise ValueError(f"step {index} ({step_type}) requires a prompt")
    return Step(
        type=step_type,
        selector=raw.get("selector"),
        value=raw.get("value"),
        url=raw.get("url"),
        name=raw.get("name"),
        timeout_ms=raw.get("timeout_ms") or raw.get("timeout"),
        prompt=raw.get("prompt"),
    )


def _parse_behavior(raw: dict[str, Any]) -> Behavior:
    missing = [k for k in REQUIRED_TOP if k not in raw]
    if missing:
        raise ValueError(f"behavior missing required keys: {', '.join(missing)}")
    steps_raw = raw["steps"]
    if not isinstance(steps_raw, list):
        raise ValueError("behavior 'steps' must be a list")
    steps = [_build_step(s, i) for i, s in enumerate(steps_raw)]
    return Behavior(name=raw["name"], steps=steps, raw=raw)


def load_behavior(path: str | Path) -> Behavior:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"behavior file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"malformed YAML in {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"behavior root must be a mapping, got {type(raw).__name__}")
    return _parse_behavior(raw)


def load_behavior_or_none(path: str | Path) -> Behavior | None:
    try:
        return load_behavior(path)
    except FileNotFoundError:
        return None


_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}|\$([A-Z0-9_]+)")


def _strip_russian_country_code(phone: str) -> str:
    """Strip a Russian country code so the number suits forms that prepend +7.

    Handles ``+7 906...``, ``7906...``, ``8 906...`` (alt trunk prefix).
    Leaves non-Russian numbers unchanged. Returns digits only, no spaces.
    """
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits[0] in ("7", "8"):
        return digits[1:]
    if len(digits) == 12 and digits.startswith("7"):
        # +7XXXXXXXXXX already stripped of "+"
        return digits[1:]
    return digits or (phone or "")


def build_var_map(
    profile: Any,
    extra_vars: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build a $VARIABLE -> value map from an actor profile.

    Recognized variables:
      $ACTOR_NAME        -> profile.name
      $ACTOR_LOGIN       -> profile.credentials.login
      $ACTOR_PHONE       -> profile.credentials.login (alias for phone-style logins)
      $ACTOR_PHONE_LOCAL -> phone with Russian country code stripped, for forms
                            that prepend +7 themselves (e.g. WB auth)
      $ACTOR_PASSWORD    -> resolved password (env var value, not the var name)
      $ACTOR_CODE        -> SMS code provided out-of-band via wait_input

    Optional ``extra_vars`` are merged on top of the profile-derived map so
    that runtime-supplied values (e.g. an operator-entered SMS code) can be
    substituted into later steps.
    """
    creds = getattr(profile, "credentials", None)
    var_map: dict[str, str] = {
        "ACTOR_NAME": getattr(profile, "name", "") or "",
    }
    if creds is not None:
        login = getattr(creds, "login", "") or ""
        var_map["ACTOR_LOGIN"] = login
        var_map["ACTOR_PHONE"] = login
        var_map["ACTOR_PHONE_LOCAL"] = _strip_russian_country_code(login)
        password = getattr(creds, "password", "")
        if callable(password):  # defensive: property already returns str
            try:
                password = password()
            except Exception:
                password = ""
        var_map["ACTOR_PASSWORD"] = password or ""
    if extra_vars:
        var_map.update(extra_vars)
    return var_map


def substitute_variables(text: str | None, var_map: dict[str, str]) -> str | None:
    if text is None:
        return None

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2)
        return var_map.get(key, match.group(0))

    return _VAR_PATTERN.sub(_replace, text)


def apply_variables(behavior: Behavior, var_map: dict[str, str]) -> Behavior:
    """Return a new Behavior with $VARIABLE references resolved in every step."""
    substituted: list[Step] = []
    for step in behavior.steps:
        substituted.append(
            Step(
                type=step.type,
                selector=substitute_variables(step.selector, var_map),
                value=substitute_variables(step.value, var_map),
                url=substitute_variables(step.url, var_map),
                name=substitute_variables(step.name, var_map),
                timeout_ms=step.timeout_ms,
                prompt=substitute_variables(step.prompt, var_map),
            )
        )
    return Behavior(name=behavior.name, steps=substituted, raw=behavior.raw)
