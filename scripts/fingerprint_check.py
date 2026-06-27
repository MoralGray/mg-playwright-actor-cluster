from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
ARCHIVE_DIR = OUTPUT_DIR / "archive" / "fingerprint"
ACTORS_DIR = Path(__file__).resolve().parent.parent / "configs" / "actors"

# Run via `python scripts/fingerprint_check.py` from the repo root; ensure
# the repo root is importable so `browser`, `swarm` etc. resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BROWSERLEAKS_URL = "https://browserleaks.com"
FINGERPRINTJS_URL = "https://fingerprintjs.github.io/fingerprintjs/"

# Browserleaks sub-pages to crawl. Each entry is (slug, screenshot_name).
# The landing page (/) plus dedicated sub-pages covering the full fingerprint
# surface Facct or any anti-bot SDK may probe.
BROWSERLEAKS_SUBPAGES: list[tuple[str, str]] = [
    ("", "browserleaks_landing"),
    ("/ip", "browserleaks_ip"),
    ("/javascript", "browserleaks_javascript"),
    ("/webrtc", "browserleaks_webrtc"),
    ("/canvas", "browserleaks_canvas"),
    ("/webgl", "browserleaks_webgl"),
    ("/fonts", "browserleaks_fonts"),
    ("/geo", "browserleaks_geo"),
    ("/features", "browserleaks_features"),
    ("/tls", "browserleaks_tls"),
    ("/proxy", "browserleaks_proxy"),
    ("/client-hints", "browserleaks_client_hints"),
    ("/rects", "browserleaks_rects"),
    ("/chrome", "browserleaks_chrome"),
    ("/dns", "browserleaks_dns"),
]

# Generic per-page extractor. Browserleaks pages render results in tables and
# `.card`/`.result` containers. We collect every key/value row from tables,
# plus all `.card`/`.result` block text, plus a few well-known result
# selectors per page. Returns a dict with `url`, `page`, `tables`, `cards`,
# `resultText`.
PER_PAGE_EXTRACT_JS = """(pageSlug) => {
  const result = {url: location.href, page: pageSlug, tables: [], cards: [], resultText: ""};
  try {
    // All visible tables -> rows of cells
    const tables = Array.from(document.querySelectorAll('table'));
    result.tables = tables.map((t) => {
      const rows = Array.from(t.querySelectorAll('tr'));
      return rows.map((tr) =>
        Array.from(tr.querySelectorAll('th,td')).map((c) => (c.innerText || '').trim())
      );
    });
  } catch (e) { result.tables = {error: String(e)}; }

  try {
    // Cards / result blocks (browserleaks uses .card and various result ids)
    const blocks = Array.from(document.querySelectorAll(
      '.card, .result, #result, #ip, #canvas, #webgl, #webrtc, #fonts, ' +
      '#javascript, #features, #tls, #proxy, #client-hints, #rects, #chrome, ' +
      '#dns, #geo, .table-responsive, .panel'
    ));
    result.cards = blocks.map((b) => (b.innerText || '').trim())
      .filter((t) => t.length > 0).slice(0, 40);
  } catch (e) { result.cards = {error: String(e)}; }

  try {
    // Aggregate visible text from the main content area (best-effort)
    const main = document.querySelector('main, .container, body');
    result.resultText = main
      ? main.innerText.slice(0, 8000)
      : document.body.innerText.slice(0, 8000);
  } catch (e) { result.resultText = ""; }

  // A few per-page specifics
  try {
    if (pageSlug === '/canvas' || pageSlug === '') {
      const sig = document.querySelector('#canvas-signature, [id*="canvas"] code, .signature');
      if (sig) result.canvasSignature = sig.textContent.trim();
    }
    if (pageSlug === '/webgl' || pageSlug === '') {
      const vendor = document.querySelector('#webgl-vendor, [id*="vendor"]');
      const renderer = document.querySelector('#webgl-renderer, [id*="renderer"]');
      if (vendor) result.webglVendor = vendor.textContent.trim();
      if (renderer) result.webglRenderer = renderer.textContent.trim();
    }
    if (pageSlug === '/ip' || pageSlug === '') {
      const ip = document.querySelector('#ip, [id*="ip-address"], .ip-address');
      if (ip) result.ip = ip.textContent.trim();
    }
  } catch (e) { result.specifics_error = String(e); }

  return result;
}"""

# JS that collects the FingerprintJS visitor data shown on the demo page.
# The live demo at fingerprintjs.github.io shows the visitorId plus a
# component breakdown card; we extract both.
FINGERPRINTJS_COLLECT_JS = """() => {
  const result = {url: location.href};
  try {
    const visitorEl = document.querySelector('#visitorId, .visitor-id, [data-testid="visitorId"]');
    result.visitorId = visitorEl ? visitorEl.textContent.trim() : null;
  } catch (e) { result.visitorId = {error: String(e)}; }
  try {
    // Component breakdowns are rendered as definition lists / cards
    const components = {};
    const rows = Array.from(document.querySelectorAll('dl, .component, .row, .card'));
    for (const row of rows) {
      const dt = row.querySelector('dt, .component-key, strong');
      const dd = row.querySelector('dd, .component-value, code');
      if (dt && dd) {
        const key = dt.textContent.trim();
        const val = dd.textContent.trim();
        if (key && val) components[key] = val;
      }
    }
    result.components = components;
  } catch (e) { result.components = {error: String(e)}; }
  try {
    // Full component text (fallback / for hash extraction)
    const cards = Array.from(
      document.querySelectorAll('.container .row, .card, [class*="component"]')
    );
    result.componentText = cards.map((c) => c.innerText.trim())
      .filter((t) => t.length > 0).slice(0, 80);
  } catch (e) { result.componentText = {error: String(e)}; }
  try {
    result.bodyText = document.body.innerText.slice(0, 6000);
  } catch (e) { result.bodyText = {error: String(e)}; }
  try {
    // Any hash-like strings on the page (visitorId is a 20-char hash)
    const text = document.body.innerText;
    const hashMatches = text.match(/\\b[a-f0-9]{16,}\\b/gi) || [];
    result.hashes = Array.from(new Set(hashMatches)).slice(0, 10);
  } catch (e) { result.hashes = {error: String(e)}; }
  return result;
}"""

# System prompt for the LLM leak analysis (Russian, per epic spec).
# RUF001 false-positives on Cyrillic letters inside Russian text.
SYSTEM_PROMPT = (
    "Ты эксперт по browser fingerprinting. Проанализируй следующие данные с "  # noqa: RUF001
    "browserleaks.com и FingerprintJS. Найди утечки, несоответствия, признаки "
    "автоматизации, которые может обнаружить Facct или другой антибот SDK. "
    "Для каждого найденного: опиши проблему, что должен показывать реальный "
    "Chrome вместо этого, приоритет (high/medium/low)."
)


def _run_dir() -> Path:
    """Create a timestamped subfolder for this run and return its path."""
    stamp = datetime.now().strftime("fingerprint-%d-%m-%Y-%H-%M-%S-%f")
    run_dir = OUTPUT_DIR / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


async def _collect_browserleaks_subpage(
    page, slug: str, screenshot_name: str, run_dir: Path
) -> dict[str, Any]:
    """Navigate to one browserleaks sub-page, wait, screenshot, extract data."""
    url = BROWSERLEAKS_URL + slug
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_selector("body", timeout=20000)
    # Wait for result containers (tables / cards) to render, then settle 2s
    # so async JS tests (canvas/webgl/webrtc) have time to populate.
    with contextlib.suppress(Exception):
        await page.wait_for_selector("table, .card, .result, #result", timeout=10000)
    await asyncio.sleep(2)
    await page.screenshot(path=str(run_dir / f"{screenshot_name}.png"))
    data = await page.evaluate(PER_PAGE_EXTRACT_JS, slug)
    return data


async def _collect_browserleaks(page, run_dir: Path) -> dict[str, dict[str, Any]]:
    """Crawl all browserleaks sub-pages; return {slug: page_data}."""
    results: dict[str, dict[str, Any]] = {}
    total = len(BROWSERLEAKS_SUBPAGES)
    for i, (slug, screenshot_name) in enumerate(BROWSERLEAKS_SUBPAGES, 1):
        key = slug or "/"
        display = slug or "/"
        print(f"[fingerprint-check] browserleaks {i}/{total} - {display}")
        try:
            data = await _collect_browserleaks_subpage(page, slug, screenshot_name, run_dir)
            results[key] = data
            tables = len(data.get("tables", []))
            cards = len(data.get("cards", []))
            print(f"[fingerprint-check]   done {display} ({tables} tables, {cards} cards)")
        except Exception as exc:
            results[key] = {"url": BROWSERLEAKS_URL + slug, "error": str(exc)}
            print(f"[fingerprint-check]   error {display}: {exc}")
    return results


async def _collect_fingerprintjs(page, run_dir: Path) -> dict[str, Any]:
    """Navigate to the FingerprintJS demo, screenshot, collect visitor data."""
    print("[fingerprint-check] visiting fingerprintjs...")
    await page.goto(FINGERPRINTJS_URL, wait_until="domcontentloaded")
    await page.wait_for_selector("body", timeout=20000)
    await page.screenshot(path=str(run_dir / "fingerprintjs_landing.png"))
    with contextlib.suppress(Exception):
        await page.wait_for_selector("#visitorId, .visitor-id, .container", timeout=15000)
    await asyncio.sleep(2)
    await page.screenshot(path=str(run_dir / "fingerprintjs_result.png"))
    data = await page.evaluate(FINGERPRINTJS_COLLECT_JS)
    visitor_id = data.get("visitorId") if isinstance(data, dict) else None
    print(f"[fingerprint-check]   visitorId: {visitor_id}")
    return data
    return data


def _save_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _run_llm_audit(aggregated: dict[str, Any]) -> str:
    """Send aggregated fingerprint data to OpenRouter, return the model answer."""
    token = os.environ.get("OPENROUTER_TOKEN", "")
    model = os.environ.get("OPENROUTER_MODEL", "")
    if not token or not model:
        sys.exit("OPENROUTER_TOKEN and OPENROUTER_MODEL must be set (see mise.toml)")

    from openrouter import OpenRouter

    client = OpenRouter(api_key=token)
    user_content = json.dumps(aggregated, indent=2, ensure_ascii=False, default=str)
    # Cap user prompt size to keep the request under model context limits.
    if len(user_content) > 50000:
        user_content = user_content[:50000] + "\n... (truncated)"
    resp = client.chat.send(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None) if message else None
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return content or ""


async def main() -> int:
    from playwright.async_api import async_playwright

    from browser.context import create_context
    from swarm.profile import load_actor

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    run_dir = _run_dir()
    headless = os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() != "false"
    actor_name = os.environ.get("FINGERPRINT_CHECK_ACTOR", "browserleaks_check")
    print(f"[fingerprint-check] run folder: {run_dir}")
    print(f"[fingerprint-check] actor profile: {actor_name}")
    print(f"[fingerprint-check] headless: {headless}")

    # Use the browserleaks_check actor profile to drive the stealth context;
    # both sites are visited in the same browser context so the fingerprint
    # is consistent across the two collectors.
    profile = load_actor(actor_name, ACTORS_DIR)

    async with async_playwright() as pw:
        browser, context, _rf, _cp = await create_context(
            pw,
            profile,
            headless=os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() != "false",
        )
        page = await context.new_page()
        try:
            browserleaks_data = await _collect_browserleaks(page, run_dir)
            fingerprintjs_data = await _collect_fingerprintjs(page, run_dir)
        finally:
            await context.close()
            await browser.close()

    # Per sub-page JSON files in the run dir (e.g. browserleaks_canvas.json).
    print(f"[fingerprint-check] saving {len(browserleaks_data)} sub-page JSON files...")
    for slug, data in browserleaks_data.items():
        safe = slug.strip("/") or "landing"
        page_name = "browserleaks" if slug == "/" else f"browserleaks_{safe.replace('-', '_')}"
        _save_json(run_dir / f"{page_name}.json", data)
        print(f"[fingerprint-check] browserleaks {slug or '/'} -> {run_dir / f'{page_name}.json'}")

    # FingerprintJS result JSON.
    _save_json(run_dir / "fingerprintjs_result.json", fingerprintjs_data)
    print(f"[fingerprint-check] fingerprintjs -> {run_dir / 'fingerprintjs_result.json'}")

    # Archive copies.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    _save_json(ARCHIVE_DIR / f"browserleaks-{stamp}.json", browserleaks_data)
    _save_json(ARCHIVE_DIR / f"fingerprintjs-{stamp}.json", fingerprintjs_data)

    # Aggregate everything for the LLM audit.
    aggregated = {
        "browserleaks": browserleaks_data,
        "fingerprintjs": fingerprintjs_data,
        "collected_at": datetime.now().isoformat(),
    }
    _save_json(run_dir / "fingerprint_aggregated.json", aggregated)

    # Run the LLM leak analysis.
    model = os.environ.get("OPENROUTER_MODEL", "unknown")
    print(f"[fingerprint-check] running LLM analysis (model: {model})...")
    audit = _run_llm_audit(aggregated)
    audit_path = run_dir / "fingerprint_audit_llm_output.txt"
    audit_path.write_text(audit, encoding="utf-8")
    print(f"[fingerprint-check] LLM analysis done ({len(audit)} chars)")

    # Summary to stdout.
    subpages_visited = len(browserleaks_data)
    ok_count = sum(1 for v in browserleaks_data.values() if "error" not in v)
    err_count = subpages_visited - ok_count
    print()
    print("=== fingerprint audit summary ===")
    print(f"browserleaks sub-pages visited: {subpages_visited} ({ok_count} ok, {err_count} errors)")
    print(f"fingerprintjs visitorId: {fingerprintjs_data.get('visitorId')}")
    print(f"LLM analysis saved to: {audit_path}")
    print(f"run folder: {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
