from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROXIES_PATH = Path(__file__).resolve().parent.parent / "configs" / "proxies.json"


@dataclass(slots=True)
class Proxy:
    ref: str
    endpoint: str
    provider: str
    banned: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def available(self) -> bool:
        return not self.banned


class ProxyUnavailable(Exception):
    """Raised when no non-banned proxy can satisfy an acquire request."""


def _parse_proxy(raw: dict[str, Any]) -> Proxy:
    required = ("ref", "endpoint")
    missing = [k for k in required if k not in raw]
    if missing:
        raise ValueError(f"proxy entry missing fields: {', '.join(missing)}")
    return Proxy(
        ref=raw["ref"],
        endpoint=raw["endpoint"],
        provider=raw.get("provider", ""),
        banned=bool(raw.get("banned", False)),
    )


def load_proxies(path: Path = PROXIES_PATH) -> list[Proxy]:
    if not path.exists():
        return []
    raw_list = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_list, list):
        raise ValueError(f"proxies file must be a JSON list, got {type(raw_list).__name__}")
    return [_parse_proxy(item) for item in raw_list]


class ProxyPool:
    """In-process proxy pool guarding acquire/release/ban rotation.

    Proxies are referenced by `ref` (matching actor profile `proxy_ref`).
    `acquire(ref)` returns a specific proxy if available; `acquire()` returns
    the next available non-banned proxy (round-robin over non-banned entries).
    """

    def __init__(self, proxies: list[Proxy] | None = None) -> None:
        self._proxies: dict[str, Proxy] = {p.ref: p for p in (proxies or [])}
        self._lock = asyncio.Lock()
        self._cursor = 0

    def __len__(self) -> int:
        return len(self._proxies)

    def refs(self) -> list[str]:
        return list(self._proxies.keys())

    def get(self, ref: str) -> Proxy | None:
        return self._proxies.get(ref)

    def is_available(self, ref: str) -> bool:
        proxy = self._proxies.get(ref)
        return proxy is not None and proxy.available

    def status(self) -> list[Proxy]:
        return [self._copy(p) for p in self._proxies.values()]

    async def acquire(self, ref: str | None = None) -> Proxy:
        async with self._lock:
            if ref is not None:
                proxy = self._proxies.get(ref)
                if proxy is None:
                    raise ProxyUnavailable(f"proxy not registered: {ref}")
                if not proxy.available:
                    raise ProxyUnavailable(f"proxy banned: {ref}")
                return self._copy(proxy)
            available = [p for p in self._proxies.values() if p.available]
            if not available:
                raise ProxyUnavailable("no available proxies in pool")
            idx = self._cursor % len(available)
            self._cursor = (self._cursor + 1) % len(available)
            return self._copy(available[idx])

    async def release(self, proxy: Proxy) -> None:
        # Stateless release; banned proxies stay banned. Kept for symmetry.
        async with self._lock:
            existing = self._proxies.get(proxy.ref)
            if existing is not None and existing.banned:
                return

    async def mark_banned(self, ref: str) -> Proxy | None:
        async with self._lock:
            proxy = self._proxies.get(ref)
            if proxy is None:
                return None
            proxy.banned = True
            return self._copy(proxy)

    @staticmethod
    def _copy(proxy: Proxy) -> Proxy:
        return Proxy(
            ref=proxy.ref,
            endpoint=proxy.endpoint,
            provider=proxy.provider,
            banned=proxy.banned,
        )


__all__ = ["PROXIES_PATH", "Proxy", "ProxyPool", "ProxyUnavailable", "load_proxies"]
