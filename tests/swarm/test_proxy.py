from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from swarm.proxy import (
    Proxy,
    ProxyPool,
    ProxyUnavailable,
    load_proxies,
)

PROXIES_FILE = Path(__file__).resolve().parent.parent.parent / "configs" / "proxies.json"


def _run(coro):
    return asyncio.run(coro)


class TestLoadProxies(unittest.TestCase):
    def test_load_real_config(self) -> None:
        proxies = load_proxies(PROXIES_FILE)
        self.assertGreater(len(proxies), 0)
        raw = json.loads(PROXIES_FILE.read_text(encoding="utf-8"))
        raw_by_ref = {r["ref"]: r for r in raw}
        self.assertEqual(len(proxies), len(raw_by_ref))
        for p in proxies:
            self.assertIn(p.ref, raw_by_ref)
            self.assertEqual(p.endpoint, raw_by_ref[p.ref]["endpoint"])
            self.assertEqual(p.banned, raw_by_ref[p.ref].get("banned", False))
            self.assertTrue(p.endpoint)

    def test_load_missing_file_returns_empty(self) -> None:
        proxies = load_proxies(Path("/nonexistent/path/proxies.json"))
        self.assertEqual(proxies, [])

    def test_load_malformed_entry_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proxies.json"
            path.write_text(json.dumps([{"endpoint": "only"}]), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_proxies(path)

    def test_load_non_list_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proxies.json"
            path.write_text(json.dumps({"ref": "x"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_proxies(path)


class TestProxyPoolAcquire(unittest.TestCase):
    def _pool(self) -> ProxyPool:
        return ProxyPool(
            [
                Proxy(
                    ref="proxy-ru-1",
                    endpoint="socks5://147.45.231.206:1080",
                    provider="free-ru",
                ),
                Proxy(
                    ref="proxy-ru-6",
                    endpoint="http://92.242.41.77:443",
                    provider="free-ru",
                ),
                Proxy(
                    ref="proxy-ru-2",
                    endpoint="socks5://193.233.139.106:1080",
                    provider="free-ru",
                ),
            ]
        )

    def test_acquire_by_ref(self) -> None:
        pool = self._pool()
        proxy = _run(pool.acquire("proxy-ru-6"))
        self.assertEqual(proxy.ref, "proxy-ru-6")
        self.assertEqual(proxy.endpoint, "http://92.242.41.77:443")

    def test_acquire_by_unknown_ref_raises(self) -> None:
        pool = self._pool()
        with self.assertRaises(ProxyUnavailable):
            _run(pool.acquire("zzz"))

    def test_acquire_round_robin_over_available(self) -> None:
        pool = self._pool()
        first = _run(pool.acquire())
        second = _run(pool.acquire())
        third = _run(pool.acquire())
        refs = [first.ref, second.ref, third.ref]
        self.assertEqual(sorted(refs), ["proxy-ru-1", "proxy-ru-2", "proxy-ru-6"])

    def test_acquire_skips_banned(self) -> None:
        pool = self._pool()
        _run(pool.mark_banned("proxy-ru-1"))
        _run(pool.mark_banned("proxy-ru-2"))
        for _ in range(4):
            proxy = _run(pool.acquire())
            self.assertEqual(proxy.ref, "proxy-ru-6")

    def test_acquire_banned_by_ref_raises(self) -> None:
        pool = self._pool()
        _run(pool.mark_banned("proxy-ru-1"))
        with self.assertRaises(ProxyUnavailable):
            _run(pool.acquire("proxy-ru-1"))

    def test_acquire_exhaustion_raises(self) -> None:
        pool = self._pool()
        for ref in ("proxy-ru-1", "proxy-ru-6", "proxy-ru-2"):
            _run(pool.mark_banned(ref))
        with self.assertRaises(ProxyUnavailable):
            _run(pool.acquire())

    def test_mark_banned_returns_copy(self) -> None:
        pool = self._pool()
        banned = _run(pool.mark_banned("proxy-ru-1"))
        self.assertIsNotNone(banned)
        self.assertTrue(banned.banned)
        self.assertTrue(pool.get("proxy-ru-1").banned)

    def test_is_available(self) -> None:
        pool = self._pool()
        self.assertTrue(pool.is_available("proxy-ru-1"))
        _run(pool.mark_banned("proxy-ru-1"))
        self.assertFalse(pool.is_available("proxy-ru-1"))
        self.assertFalse(pool.is_available("zzz"))

    def test_status_returns_copies(self) -> None:
        pool = self._pool()
        status = pool.status()
        self.assertEqual(len(status), 3)
        status[0].banned = True
        self.assertFalse(pool.get("proxy-ru-1").banned)

    def test_release_is_noop_for_banned(self) -> None:
        pool = self._pool()
        proxy = _run(pool.acquire("proxy-ru-1"))
        _run(pool.mark_banned("proxy-ru-1"))
        _run(pool.release(proxy))
        self.assertTrue(pool.get("proxy-ru-1").banned)

    def test_empty_pool_acquire_raises(self) -> None:
        pool = ProxyPool([])
        with self.assertRaises(ProxyUnavailable):
            _run(pool.acquire())


if __name__ == "__main__":
    unittest.main()
