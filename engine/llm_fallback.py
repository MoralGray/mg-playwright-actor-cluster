from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from playwright.async_api import Page

DOM_MAX_CHARS = 8000
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"

CAPTCHA_HINTS = (
    "captcha",
    "g-recaptcha",
    "h-captcha",
    "cf-turnstile",
    "arkose",
    "funcaptcha",
    "challenge-form",
    "px-captcha",
)

_POPUP_OVERLAY_QUERY = """() => {
  const els = document.querySelectorAll(
    'div[role=dialog], [class*=modal i], [class*=popup i], [class*=overlay i], [class*=dialog i]'
  );
  const out = [];
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width > 100 && r.height > 80) {
      const cls = (el.className && el.className.toString) ? el.className.toString() : '';
      const text = (el.innerText || '').slice(0, 200);
      out.push({tag: el.tagName, cls, text});
    }
  }
  return out.slice(0, 5);
}"""


class LLMClient(Protocol):
    async def complete(self, prompt: str) -> str: ...


def _strip_fences(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:css|json|html)?\s*(.*?)```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    return text.strip().strip("`").strip('"').strip("'")


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return text.strip()


async def _capture_dom(page: Page) -> str:
    try:
        html = await page.evaluate("() => document.documentElement.outerHTML")
    except Exception:
        return ""
    if len(html) > DOM_MAX_CHARS:
        html = html[:DOM_MAX_CHARS] + "\n<!-- truncated -->"
    return html


async def _capture_screenshot_b64(page: Page) -> str | None:
    try:
        return await page.screenshot(type="png")
    except Exception:
        return None


class OpenRouterLLM:
    """Thin async wrapper over the OpenRouter SDK chat completion endpoint.

    The SDK is imported lazily so the module imports cleanly without the
    dependency installed (tests inject a fake LLMClient).
    """

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        # Prefer OPENROUTER_TOKEN (set in mise.toml); fall back to
        # OPENROUTER_API_KEY for compatibility with older deployments.
        self._api_key = (
            api_key
            or os.environ.get("OPENROUTER_TOKEN")
            or os.environ.get("OPENROUTER_API_KEY", "")
        )
        self._model = model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL
        self._client = None

    def _ensure_client(self) -> object:
        if self._client is None:
            from openrouter import OpenRouter

            self._client = OpenRouter(api_key=self._api_key)
        return self._client

    async def complete(self, prompt: str) -> str:
        client = self._ensure_client()
        resp = await client.chat.send_async(  # type: ignore[union-attr]
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
        )
        choices = getattr(resp, "choices", None) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None) if message else None
        if content is None and isinstance(message, dict):
            content = message.get("content")
        return content or ""


async def remap_selector(
    client: LLMClient,
    page: Page,
    intent: str,
    original_selector: str,
) -> str | None:
    """Ask the LLM for a new CSS selector given the current DOM + intent.

    Returns a cleaned selector string, or None if the LLM could not help.
    """
    dom = await _capture_dom(page)
    prompt = (
        "You are a web automation selector repair assistant. "
        "A deterministic CSS/XPath selector failed to resolve on the page.\n"
        f"Intent: {intent}\n"
        f"Original selector: {original_selector}\n\n"
        "Given the DOM fragment below, return ONE valid CSS selector that matches "
        "the intended element. Reply with only the selector inside a single fenced "
        "css block, no explanation.\n\n"
        f"<dom>\n{dom}\n</dom>"
    )
    raw = await client.complete(prompt)
    cleaned = _first_line(_strip_fences(raw))
    if not cleaned or len(cleaned) > 500 or "<" in cleaned:
        return None
    return cleaned


async def detect_popup(page: Page) -> list[dict[str, str]]:
    """Heuristic detection of unexpected visible overlays/popups."""
    try:
        return await page.evaluate(_POPUP_OVERLAY_QUERY)
    except Exception:
        return []


async def dismiss_popup(
    client: LLMClient | None,
    page: Page,
    predefined_dismiss: tuple[str, ...] = (),
) -> str | None:
    """Try predefined dismiss selectors first, then LLM vision fallback.

    Returns the selector that dismissed the popup, or None. Predefined
    selectors are attempted unconditionally (they are cheap and require no
    LLM client); the LLM vision fallback only runs when a client is provided.
    """
    for sel in predefined_dismiss:
        try:
            if await page.locator(sel).count() > 0:
                await page.click(sel, timeout=2000)
                return sel
        except Exception:
            continue
    if client is None:
        return None
    overlays = await detect_popup(page)
    if not overlays:
        return None
    prompt = (
        "You are a popup dismissal assistant. Visible overlays were detected on the "
        'page. Return a JSON object {"selector": "<css>", "action": "click"} '
        "describing how to dismiss the most prominent overlay. Reply with only the "
        f"JSON.\n\nOverlays: {json.dumps(overlays)[:1500]}"
    )
    raw = await client.complete(prompt)
    cleaned = _strip_fences(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    sel = data.get("selector") if isinstance(data, dict) else None
    action = data.get("action", "click") if isinstance(data, dict) else "click"
    if not sel or not isinstance(sel, str):
        return None
    try:
        if action == "click":
            await page.click(sel, timeout=3000)
        return sel
    except Exception:
        return None


async def detect_captcha(page: Page) -> bool:
    """Heuristic captcha detection by known element hints in the DOM."""
    try:
        html = await page.content()
    except Exception:
        return False
    low = html.lower()
    return any(hint in low for hint in CAPTCHA_HINTS)


async def confirm_captcha(
    client: LLMClient,
    page: Page,
) -> bool:
    """LLM confirmation that a captcha challenge is present (cost guard)."""
    dom = await _capture_dom(page)
    prompt = (
        "Inspect the DOM fragment and answer with a single word: TRUE if a CAPTCHA "
        "or human-verification challenge is present, FALSE otherwise.\n\n"
        f"<dom>\n{dom}\n</dom>"
    )
    raw = (await client.complete(prompt)).strip().lower()
    return raw.startswith("true")


@dataclass(frozen=True, slots=True)
class CaptchaDecision:
    is_captcha: bool
    action: str  # "pause" | "proceed"


async def resolve_captcha(
    client: LLMClient | None,
    page: Page,
) -> CaptchaDecision:
    """Heuristic check, then LLM confirmation if a client is provided."""
    if not await detect_captcha(page):
        return CaptchaDecision(is_captcha=False, action="proceed")
    if client is None:
        return CaptchaDecision(is_captcha=True, action="pause")
    confirmed = await confirm_captcha(client, page)
    if confirmed:
        return CaptchaDecision(is_captcha=True, action="pause")
    return CaptchaDecision(is_captcha=False, action="proceed")


__all__ = [
    "CaptchaDecision",
    "LLMClient",
    "OpenRouterLLM",
    "confirm_captcha",
    "detect_captcha",
    "detect_popup",
    "dismiss_popup",
    "remap_selector",
    "resolve_captcha",
]
