"""Run both WB actors end-to-end and dump the popular-search-queries table.

Boots the FastAPI server in-process, spawns the two WB actors
(``wildberries_user_deterministic``) which logs in
via phone + SMS code, navigate to the popular-search-queries analytics page
and extract the table (deterministic actor via JS evaluate, LLM actor via
OpenRouter). The operator is prompted on stdin for the SMS code once the
actors reach the ``wait_input`` step; the code is delivered through
``POST /actors/{name}/input``. After both actors finish, the extracted rows
are read from the SQLite logs table and printed.

Analog of ``scripts/extract_analytics.py`` but driven by the Actor/swarm
infrastructure so it exercises resolve_mode + wait_input end-to-end.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "swarm.db"
OUTPUT_DIR = REPO_ROOT / "output"
SERVER_URL = "http://127.0.0.1:8000"
ACTORS = ("wildberries_user_deterministic",)
SMS_PHONE = os.environ.get("WILBERRIES_USER_DETERMINISTIC_PHONE")
if not SMS_PHONE:
    print("[wb-analytics] WILBERRIES_USER_DETERMINISTIC_PHONE is not set", file=sys.stderr)
    sys.exit(1)

# Polling cadence.
POLL_INTERVAL_S = 1.0
POLL_TIMEOUT_S = 600.0


def _log(msg: str) -> None:
    print(f"[wb-analytics] {msg}", flush=True)


def _run_dir() -> Path:
    """Create a timestamped subfolder for this run and return its path."""
    stamp = datetime.now().strftime("wb-%d-%m-%Y-%H-%M-%S-%f")
    run_dir = OUTPUT_DIR / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _dump_last_logs(actor_id: str, count: int = 5) -> None:
    logs = _query_recent_logs(actor_id, limit=count)
    for row in reversed(logs):
        _log(f"  [{row['level']:5s}] {row['step_name']:40s} {row['message'][:80]}")


def _read_sms_code() -> str:
    print(f"[wb-analytics] Enter SMS code sent to {SMS_PHONE}: ", end="", flush=True)
    return sys.stdin.readline().strip()


async def _wait_for_server(client: httpx.AsyncClient) -> None:
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        try:
            r = await client.get(f"{SERVER_URL}/health")
            if r.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.3)
    raise RuntimeError("FastAPI server did not become healthy in 30s")


async def _actor_state(client: httpx.AsyncClient, name: str) -> str | None:
    r = await client.get(f"{SERVER_URL}/actors/{name}")
    if r.status_code != 200:
        return None
    return r.json().get("state")


async def _wait_for_wait_input(client: httpx.AsyncClient, name: str) -> bool:
    """Wait until the actor session logs a ``wait_input`` step.

    We query the logs table for the most recent session and look for a
    step_name matching ``step[N].wait_input`` at info level. Falls back to
    inspecting the actor state for the ACTION state if no log row exists yet.
    """
    deadline = time.monotonic() + POLL_TIMEOUT_S
    last_status = 0.0
    while time.monotonic() < deadline:
        logs = _query_recent_logs(name, limit=50)
        for row in logs:
            step_name = row.get("step_name", "") or ""
            level = row.get("level", "")
            if "wait_input" in step_name and level == "info":
                return True
        # Exit if actor completed without wait_input (cookies path)
        r = await client.get(f"{SERVER_URL}/actors/{name}")
        if r.status_code == 200:
            data = r.json()
            if data.get("done") and data.get("state") == "idle":
                return False
        elapsed = time.monotonic() - (deadline - POLL_TIMEOUT_S)
        if elapsed - last_status >= 30.0:
            last_status = elapsed
            r = await client.get(f"{SERVER_URL}/actors/{name}")
            state = r.json().get("state", "?") if r.status_code == 200 else "?"
            top = logs[0] if logs else {}
            sn = top.get("step_name", "")
            lv = top.get("level", "")
            msg = top.get("message", "")[:60]
            _log(f"awaiting wait_input... state={state} {elapsed:.0f}s last={sn} {lv} {msg}")
        await asyncio.sleep(POLL_INTERVAL_S)
    _log(f"timeout waiting for wait_input; last logs for {name}:")
    _dump_last_logs(name, 5)
    return False


def _query_recent_logs(actor_id: str, limit: int = 100) -> list[dict[str, Any]]:
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cursor = conn.execute(
            "SELECT step_name, level, message, created_at "
            "FROM logs WHERE actor_id = ? ORDER BY id DESC LIMIT ?",
            (actor_id, limit),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [{"step_name": r[0], "level": r[1], "message": r[2], "created_at": r[3]} for r in rows]


def _read_extract_table_rows(actor_id: str) -> list[Any]:
    """Return the most recent extract_table JSON rows logged by ``actor_id``."""
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cursor = conn.execute(
            "SELECT message FROM logs "
            "WHERE actor_id = ? AND step_name LIKE '%extract_table%' "
            "ORDER BY id DESC",
            (actor_id,),
        )
        messages = [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()
    for msg in messages:
        try:
            return json.loads(msg)
        except (json.JSONDecodeError, TypeError):
            continue
    return []


async def _wait_actor_done(client: httpx.AsyncClient, name: str) -> str:
    """Poll until the actor task is no longer alive; return its last state."""
    deadline = time.monotonic() + POLL_TIMEOUT_S
    last_status = 0.0
    while time.monotonic() < deadline:
        r = await client.get(f"{SERVER_URL}/actors/{name}")
        if r.status_code == 200:
            data = r.json()
            if data.get("done"):
                return str(data.get("state"))
            elapsed = time.monotonic() - (deadline - POLL_TIMEOUT_S)
            if elapsed - last_status >= 30.0:
                last_status = elapsed
                logs = _query_recent_logs(name, limit=3)
                top = logs[0] if logs else {}
                st = data.get("state", "?")
                sn = top.get("step_name", "")
                lv = top.get("level", "")
                msg = top.get("message", "")[:60]
                _log(f"awaiting done... state={st} {elapsed:.0f}s last={sn} {lv} {msg}")
        else:
            return "unknown"
        await asyncio.sleep(POLL_INTERVAL_S)
    _log(f"timeout waiting for actor {name} to finish; last logs:")
    _dump_last_logs(name, 5)
    return "timeout"


async def main() -> int:
    # Boot the FastAPI server in a background subprocess so it has its own
    # event loop and process group; we kill it on exit.
    import subprocess

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_dir = _run_dir()
    _log(f"run folder: {run_dir}")

    env = os.environ.copy()
    # Route actor Logger screenshots into this run's folder so every
    # artifact of one pipeline execution lives in one place.
    env["RUN_DIR"] = str(run_dir)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "orc.server:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--log-level",
            "warning",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        # stderr=subprocess.DEVNULL,
    )
    _log(f"uvicorn pid={proc.pid} starting...")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await _wait_for_server(client)
            _log("server healthy")

            # Spawn both actors.
            for name in ACTORS:
                r = await client.post(f"{SERVER_URL}/actors/{name}/start")
                r.raise_for_status()
                _log(f"started actor {name}")
                detail = await client.get(f"{SERVER_URL}/actors/{name}")
                if detail.status_code == 200:
                    endpoint = detail.json().get("proxy_endpoint")
                    _log(f"actor {name} proxy: {endpoint}")
                from pathlib import Path

                cf = Path("cookies") / f"{name}.json"
                ce = cf.exists()
                cs = cf.stat().st_size if ce else 0
                _log(f"cookies check: {cf} exists={ce} size={cs}")

            # Wait for the deterministic actor to reach wait_input first;
            # if cookies exist the actor may complete without wait_input.
            _log("waiting for actors to reach wait_input step...")
            reached = await _wait_for_wait_input(client, ACTORS[0])
            if reached:
                code = _read_sms_code()
                if not code:
                    _log("no code entered; aborting")
                    return 1
                for name in ACTORS:
                    r = await client.post(
                        f"{SERVER_URL}/actors/{name}/input",
                        json={"key": "ACTOR_CODE", "value": code},
                    )
                    r.raise_for_status()
                    _log(f"delivered SMS code to {name}")
            else:
                # Check if actor completed without wait_input (cookies, no SMS)
                r = await client.get(f"{SERVER_URL}/actors/{ACTORS[0]}")
                if r.status_code == 200:
                    data = r.json()
                    if data.get("done") and data.get("state") == "idle":
                        _log("actor completed without wait_input (cookies); skipping SMS")
                    else:
                        _log("timeout waiting for wait_input; aborting")
                        return 1

            # Wait for each actor to complete and immediately save their data.
            for name in ACTORS:
                state = await _wait_actor_done(client, name)
                _log(f"actor {name} finished in state={state}")
                rows = _read_extract_table_rows(name)
                _log(f"=== {name}: extracted {len(rows)} table(s) ===")
                if rows:
                    rendered = json.dumps(rows, ensure_ascii=False, indent=2)
                    print(rendered)
                    safe = name.replace("/", "_")
                    out_path = run_dir / f"wb_analytics_{safe}.txt"
                    out_path.write_text(rendered, encoding="utf-8")
                    _log(f"saved {name} rows to {out_path}")
    finally:
        _log("shutting down server")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    _log(f"run folder: {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
