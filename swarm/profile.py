from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs" / "actors"


@dataclass(frozen=True, slots=True)
class Screen:
    width: int
    height: int
    offset_x: int
    offset_y: int


@dataclass(frozen=True, slots=True)
class Fingerprint:
    screen: Screen
    webgl_vendor: str
    webgl_renderer: str
    fonts: list[str]
    audio_sample_rate: int
    timezone: str
    language: str
    platform: str
    user_agent: str
    languages: list[str] = field(default_factory=lambda: ["ru-RU", "en-US", "en"])


@dataclass(frozen=True, slots=True)
class Credentials:
    url: str
    login: str
    login_env: str = ""
    password_env: str = ""

    @property
    def password(self) -> str:
        if not self.password_env:
            return ""
        return os.environ.get(self.password_env, "")


@dataclass(frozen=True, slots=True)
class ActorProfile:
    name: str
    fingerprint: Fingerprint
    credentials: Credentials
    behavior: str
    proxy_ref: str = ""
    resolve_mode: str = "selector"
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


def _build_screen(data: dict[str, Any]) -> Screen:
    s = data["screen"]
    return Screen(
        width=s["width"],
        height=s["height"],
        offset_x=s.get("offset_x", 0),
        offset_y=s.get("offset_y", 0),
    )


def _build_fingerprint(data: dict[str, Any]) -> Fingerprint:
    return Fingerprint(
        screen=_build_screen(data),
        webgl_vendor=data["webgl_vendor"],
        webgl_renderer=data["webgl_renderer"],
        fonts=list(data["fonts"]),
        audio_sample_rate=data["audio_sample_rate"],
        timezone=data["timezone"],
        language=data["language"],
        platform=data["platform"],
        user_agent=data["user_agent"],
        languages=list(data.get("languages", ["ru-RU", "en-US", "en"])),
    )


def _build_credentials(data: dict[str, Any]) -> Credentials:
    login = data.get("login", "")
    login_env = data.get("login_env", "")
    if login_env:
        login = os.environ.get(login_env, login)
    return Credentials(
        url=data["url"],
        login=login,
        login_env=login_env,
        password_env=data.get("password_env", data.get("password", "")),
    )


def _parse_profile(raw: dict[str, Any]) -> ActorProfile:
    required = ("name", "fingerprint", "credentials", "behavior")
    missing = [k for k in required if k not in raw]
    if missing:
        raise ValueError(f"actor profile missing fields: {', '.join(missing)}")
    resolve_mode = raw.get("resolve_mode", "selector")
    if resolve_mode not in ("selector", "llm"):
        raise ValueError(
            f"actor profile {raw['name']!r}: resolve_mode must be 'selector' or 'llm', "
            f"got {resolve_mode!r}"
        )
    return ActorProfile(
        name=raw["name"],
        fingerprint=_build_fingerprint(raw["fingerprint"]),
        credentials=_build_credentials(raw["credentials"]),
        behavior=raw["behavior"],
        proxy_ref=raw.get("proxy_ref", ""),
        resolve_mode=resolve_mode,
        raw=raw,
    )


def load_actor(name: str, configs_dir: Path = CONFIGS_DIR) -> ActorProfile:
    path = configs_dir / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"actor profile not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return _parse_profile(raw)
