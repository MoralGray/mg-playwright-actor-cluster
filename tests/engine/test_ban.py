from __future__ import annotations

import unittest

from engine.ban import (
    ANALYTICS_API_URL_PATTERN,
    detect_ban,
    is_analytics_api_url,
)


class FakeRequest:
    def __init__(self, resource_type: str) -> None:
        self.resource_type = resource_type


class FakeResponse:
    def __init__(self, status: int, url: str, resource_type: str = "document") -> None:
        self.status = status
        self.url = url
        self.request = FakeRequest(resource_type)


class TestDetectBan(unittest.TestCase):
    def test_ban_statuses(self) -> None:
        self.assertTrue(detect_ban(403))
        self.assertTrue(detect_ban(429))
        self.assertFalse(detect_ban(200))
        self.assertFalse(detect_ban(500))


class TestAnalyticsApiFilter(unittest.TestCase):
    def test_pattern_matches_analytics_endpoint(self) -> None:
        url = (
            "https://seller-content.wildberries.ru/ns/analytics-api/keyword-search"
            "/popular-search-queries?from=2024-01-01"
        )
        self.assertTrue(is_analytics_api_url(url))

    def test_non_analytics_url_not_filtered(self) -> None:
        self.assertFalse(is_analytics_api_url("https://seller.wildberries.ru/dashboard"))
        self.assertFalse(is_analytics_api_url(""))
        self.assertFalse(is_analytics_api_url("https://example.com"))

    def test_install_listener_skips_analytics_api(self) -> None:
        from engine.ban import install_ban_listener

        calls: list[tuple[int, str]] = []

        class _Page:
            def on(self, _event: str, handler: object) -> None:
                self.handler = handler

        page = _Page()  # type: ignore[arg-type]
        install_ban_listener(page, lambda status, url: calls.append((status, url)))  # type: ignore[arg-type]

        # 403 on a non-Facct URL fires on_ban
        page.handler(FakeResponse(403, "https://seller.wildberries.ru/dashboard"))  # type: ignore[attr-defined]
        self.assertEqual(calls, [(403, "https://seller.wildberries.ru/dashboard")])

        # 403 on the analytics API does NOT fire on_ban
        analytics_url = (
            "https://seller-content.wildberries.ru/ns/analytics-api/keyword-search"
            "/popular-search-queries"
        )
        page.handler(FakeResponse(403, analytics_url))  # type: ignore[attr-defined]
        self.assertEqual(calls, [(403, "https://seller.wildberries.ru/dashboard")])

    def test_pattern_constant_exposed(self) -> None:
        self.assertEqual(
            ANALYTICS_API_URL_PATTERN,
            "seller-content.wildberries.ru/ns/analytics-api/",
        )


if __name__ == "__main__":
    unittest.main()
