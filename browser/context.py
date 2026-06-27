from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, BrowserContext, Playwright

from browser.fingerprint import RuntimeFingerprint, apply_variance, build_init_script
from swarm.profile import ActorProfile

COOKIES_DIR = Path(__file__).resolve().parent.parent / "cookies"
logger = logging.getLogger(__name__)


def _sec_ch_ua_headers(user_agent: str) -> dict[str, str]:
    """Build Sec-CH-UA client-hint headers that match a real headed Chrome.

    Playwright/Chromium in headless mode advertises ``HeadlessChrome`` in
    ``Sec-CH-UA`` which is a hard anti-bot signal (Facct flags it). Override
    the brand list to the real Chrome surface (Google Chrome + Chromium +
    Not_A Brand) with the major version extracted from the user-agent.
    """
    import re

    m = re.search(r"Chrome/(\d+)", user_agent)
    major = m.group(1) if m else "131"
    sec_ch_ua = f'"Google Chrome";v="{major}", "Chromium";v="{major}", "Not_A Brand";v="24"'
    return {
        "Sec-CH-UA": sec_ch_ua,
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"Windows"',
    }


async def create_context(
    playwright: Playwright,
    profile: ActorProfile,
    proxy: str | None = None,
    headless: bool = True,
    rng_seed: int | None = None,
) -> tuple[Browser, BrowserContext, RuntimeFingerprint, Path | None]:
    import random

    # No rng -> apply_variance derives a stable per-actor seed so the
    # fingerprint is reproducible across sessions (anti-bot stability check).
    rng = random.Random(rng_seed) if rng_seed is not None else None
    rf = apply_variance(profile.fingerprint, rng)

    launch_kwargs: dict[str, Any] = {
        "headless": headless,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    # NO_PROXY env var disables the proxy for local debugging (1/true).
    no_proxy = os.environ.get("NO_PROXY", "").lower() in ("1", "true")
    if proxy and not no_proxy:
        launch_kwargs["proxy"] = {"server": proxy}

    browser = await playwright.chromium.launch(**launch_kwargs)
    cookies_path = COOKIES_DIR / f"{profile.name}.json"
    storage_state = str(cookies_path) if cookies_path.exists() else None
    context = await browser.new_context(
        user_agent=rf.user_agent,
        viewport={"width": rf.width, "height": rf.height},
        locale=rf.language,
        timezone_id=rf.timezone,
        extra_http_headers=_sec_ch_ua_headers(rf.user_agent),
        storage_state=storage_state,
    )
    await context.add_init_script(build_init_script(rf))
    logger.info(
        "created context for actor %s viewport=%dx%d ua=%s cookies=%s",
        profile.name,
        rf.width,
        rf.height,
        rf.user_agent,
        "loaded" if storage_state else "none",
    )
    return browser, context, rf, cookies_path
