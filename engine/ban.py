from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page, Response

logger = logging.getLogger(__name__)

BAN_STATUSES: frozenset[int] = frozenset({403, 429})

# Facct-guarded analytics API endpoints on Wildberries return 403 to bots
# that are NOT an IP ban — they are SDK-level challenge responses. Rotating
# the proxy here would burn the pool for false positives, so we skip
# proxy rotation for any URL containing this marker.
ANALYTICS_API_URL_PATTERN = "seller-content.wildberries.ru/ns/analytics-api/"


def is_analytics_api_url(url: str) -> bool:
    """Return True when the URL targets a Facct-guarded analytics API endpoint.

    These endpoints respond 403 to SDK-level challenges that are independent
    of the requesting IP, so a 403 here must not be treated as a proxy ban.
    """
    return bool(url) and ANALYTICS_API_URL_PATTERN in url


class BanDetected(Exception):
    """Raised when a 403/429 response indicates an IP ban.

    Carries the offending status code so the actor/manager can rotate the
    proxy and restart the browser context.
    """

    def __init__(self, status: int, url: str = "") -> None:
        super().__init__(f"ban detected: status={status} url={url!r}")
        self.status = status
        self.url = url


def detect_ban(status: int) -> bool:
    """Return True when an HTTP status code indicates an IP ban (403/429)."""
    return status in BAN_STATUSES


def install_ban_listener(
    page: Page,
    on_ban: Callable[[int, str], None],
) -> Callable[[Response], None]:
    """Attach a response listener that fires ``on_ban(status, url)`` on 403/429.

    Returns the handler so the caller can detach it if needed.
    """

    def _on_response(response: Response) -> None:
        try:
            status = response.status
        except Exception:
            return
        if not detect_ban(status):
            return
        # Only react to top-level document responses; 403/429 on tracking
        # pixels, images, fonts, beacon requests etc. should not rotate the
        # proxy.
        try:
            request = response.request
        except Exception:
            request = None
        resource_type = ""
        if request is not None:
            with contextlib.suppress(Exception):
                resource_type = request.resource_type
        if resource_type and resource_type not in {"document", "xhr", "fetch"}:
            return
        url = ""
        with contextlib.suppress(Exception):
            url = response.url
        # Facct anti-bot SDK on Wildberries analytics API returns 403 as a
        # challenge, not an IP ban — never rotate the proxy for these URLs.
        if is_analytics_api_url(url):
            logger.info(
                "ignoring %s on Facct-guarded analytics api %s (not a proxy ban)",
                status,
                url,
            )
            return
        logger.warning("ban response %s for %s (type=%s)", status, url, resource_type)
        try:
            on_ban(status, url)
        except Exception:
            logger.exception("on_ban callback raised")

    page.on("response", _on_response)
    return _on_response


__all__ = [
    "ANALYTICS_API_URL_PATTERN",
    "BAN_STATUSES",
    "BanDetected",
    "detect_ban",
    "install_ban_listener",
    "is_analytics_api_url",
]
