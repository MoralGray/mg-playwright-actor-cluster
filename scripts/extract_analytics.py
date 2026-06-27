# ruff: noqa: RUF001
from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "output"
TARGET_URL = "http://localhost:8111/index.test.html"

SYSTEM_PROMPT = (
    """Ты — ассистент по извлечению и форматированию данных из таблиц заказов.

Твоя задача:
1. Извлеки все email-адреса из предоставленных табличных данных.
2. Для каждого email определи, какие товары заказаны (из колонок таблицы).
3. Составь письмо-запрос на покупку этих товаров.

Формат ответа — строго по шаблону ниже. Каждый блок отделён разделителем `----`.

"""
    """"""
    """"""
    """""
----
email: <email_адрес>
----
----
product: <название или описание товара, который заказал этот клиент>
----
----
content: <текст письма с запросом на продажу или сотрудничество по этому товару,
вежливый, деловой стиль, 2-3 предложения>
----

// # ---------------------------------------------------------------------------------------


----
email: <email_адрес>
----
----
product: <название или описание товара, который заказал этот клиент>
----
----
content: <текст письма с запросом на продажу или сотрудничество по этому товару,
вежливый, деловой стиль, 2-3 предложения>
----
"""
    """"""
    """"""
    """""

Правила:
- Для каждого уникального email — отдельный блок.
- Разделитель между блоками писем "// # ----"
- Если в таблице несколько заказов от одного email — объедини товары.
- Не добавляй пояснений, вступлений или выводов — только блоки по шаблону.
- Язык: русский.
- Не используй markdown-разметку внутри блоков."""
)

# JS that collects every visible table on the page as a list of rows of cells.
_EXTRACT_TABLES_JS = """() => {
  const tables = Array.from(document.querySelectorAll('table'));
  return tables.map((t) => {
    const rows = Array.from(t.querySelectorAll('tr'));
    return rows.map((tr) =>
      Array.from(tr.querySelectorAll('th,td')).map((c) => (c.innerText || '').trim())
    );
  });
}"""


def _run_dir() -> Path:
    """Create a timestamped subfolder for this run and return its path."""
    stamp = datetime.now().strftime("extract-%d-%m-%Y-%H-%M-%S-%f")
    run_dir = OUTPUT_DIR / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


async def _login_and_capture(page, run_dir: Path) -> str:
    """Open the test page, log in, navigate to Orders, return table data text."""
    await page.goto(TARGET_URL)
    await page.wait_for_selector("#loginEmail")
    await page.screenshot(path=str(run_dir / "extract_landing.png"))

    await page.fill("#loginEmail", "test@test.com")
    await page.fill("#loginPass", "password123")
    await page.click('#loginForm button[type="submit"]')
    await page.wait_for_selector("#sidebar")
    await page.screenshot(path=str(run_dir / "extract_dashboard.png"))

    await page.click('a[onclick*="orders"]')
    await page.wait_for_selector("#pageTitle")
    await page.screenshot(path=str(run_dir / "extract_orders.png"))

    tables = await page.evaluate(_EXTRACT_TABLES_JS)
    lines: list[str] = []
    for idx, rows in enumerate(tables, 1):
        lines.append(f"--- table {idx} ---")
        for row in rows:
            lines.append(" | ".join(row))
    return "\n".join(lines)


def _run_llm(table_text: str) -> str:
    """Send system-prompt + table data to OpenRouter, return the model answer."""
    token = os.environ.get("OPENROUTER_TOKEN", "")
    model = os.environ.get("OPENROUTER_MODEL", "")
    if not token or not model:
        sys.exit("OPENROUTER_TOKEN and OPENROUTER_MODEL must be set (see mise.toml)")

    from openrouter import OpenRouter

    client = OpenRouter(api_key=token)
    resp = client.chat.send(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": table_text},
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

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_dir = _run_dir()

    # Start a local HTTP server for index.test.html so the script is
    # self-contained (no need for a separate `mise run serve`).
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", "8111", "--directory", str(REPO_ROOT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)
    try:
        async with async_playwright() as pw:
            headless = os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() != "false"
            browser = await pw.chromium.launch(headless=headless)
            page = await browser.new_page()
            try:
                table_text = await _login_and_capture(page, run_dir)
            finally:
                if not headless:
                    input("Press Enter to close browser...")
                await browser.close()

        print("=== extracted tables ===")
        print(table_text)
        print()
        print("=== LLM analytics ===")
        analytics = _run_llm(table_text)
        print(analytics)

        # Persist every artifact under the run subfolder so the pipeline's
        # results are self-contained: extracted table text + LLM analytics.
        (run_dir / "extract_analytics_tables.txt").write_text(table_text, encoding="utf-8")
        llm_output_file = run_dir / "extract_analytics_llm_output.txt"
        llm_output_file.write_text(analytics, encoding="utf-8")
        print(f"[extract] run folder: {run_dir}")
        print(f"[extract] LLM output saved to {llm_output_file}")
    finally:
        server.terminate()
        with contextlib.suppress(Exception):
            server.wait(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
