# Human Bot Swarm

Autonomous multi-actor web automation with anti-detection. Each bot impersonates a distinct human user — logs into services, navigates, extracts data, performs actions, and generates reports.

**Stack:** Python 3.12+, Playwright, FastAPI, OpenRouter SDK, SQLite
**No external services** — single asyncio process.

---

## Features

| Feature | Description |
|---------|-------------|
| Swarm Orchestration | FastAPI control API — start/stop/restart/pause/resume actors, enqueue tasks, health monitoring |
| Actor Identity | Per-actor JSON profiles (OS, browser, screen, timezone, language, WebGL) with state machine lifecycle |
| Browser Fingerprinting | 20+ JS overrides: webdriver, WebGL, canvas, audio, fonts, timezone, screen, plugins, platform, language, battery, permissions, client rects, visibility, chrome object, $cdc_ cleanup, userAgentData, getClientRects. Per-session variance applied |
| Action Engine | YAML-based step execution (navigate, click, fill, scroll, screenshot) with human-like Bezier mouse curves, log-normal delays, per-char typing |
| Error Recovery | Exponential backoff retry, LLM selector remap on layout change, popup/captcha detection, proxy rotation on IP ban |
| Proxy Pool | Round-robin proxy assignment, ban marking, per-website session limiting, NO_PROXY env var for local debug |
| LLM Fallback | OpenRouter-powered selector remapping, popup dismissal, captcha confirmation when deterministic selectors fail |
| Session Persistence | Cookie storage via Playwright storage_state — skip login on subsequent runs until session expires |
| Structured Logging | SQLite (logs, sessions, tasks) with per-step screenshots |
| Table Extraction | Standalone demo: login, extract HTML tables, send to LLM for analytics. Polling-based wait for dynamic data |
| Fingerprint Audit | Automated browserleaks.com + FingerprintJS crawl, LLM leak analysis |

---

## Actors

| Actor | What it does |
|-------|-------------|
| `wildberries_user_deterministic` | Logs into seller.wildberries.ru, navigates to popular-search-queries analytics, extracts table. Uses hardcoded CSS selectors + cookies persistence for skip-login |
| `test_user_1` | Logs into localhost:8111 (test page), navigates Orders, screenshots, scrolls |
| `test_user_2` | Same as test_user_1 with different credentials/fingerprint profile |
| `browserleaks_check` | Visits browserleaks.com sub-pages, collects fingerprint data for stealth audit |
| `fingerprintjs_check` | Visits FingerprintJS demo page, collects visitorId and component hashes |

### Running actors

- `python main.py` — starts the FastAPI server on port 8000, then control actors via REST API (POST /actors/{name}/start etc.)
- `mise run serve` — serves index.test.html on port 8111 for local testing
- `mise run extract` — standalone Playwright demo: opens localhost:8111, extracts table, sends to LLM (no swarm)
- `mise run wb-analytics` — full integration: boots server, spawns actor, prompts operator for SMS code (if no cookies), delivers via API, dumps extracted data from SQLite to .txt
- `mise run fingerprint-check` — runs fingerprint audit against browserleaks.com + FingerprintJS, sends aggregated data to LLM for leak analysis

---

## Quick Start

### 1. Run automatic setup

```bash
bash setup.sh
```

This creates `.env`, prompts for API key and phone, installs dependencies.

### 2. Start the control server

```bash
python main.py
```

Server starts on http://0.0.0.0:8000.

### 4. Use the API

```bash
# List actors
curl http://localhost:8000/actors

# Spawn an actor
curl -X POST http://localhost:8000/actors/wildberries_user_deterministic/start

# Check state
curl http://localhost:8000/actors/wildberries_user_deterministic

# When state=ACTION and you see the prompt, deliver SMS:
curl -X POST http://127.0.0.1:8000/actors/wildberries_user_deterministic/input \
  -H "Content-Type: application/json" \
  -d '{"key":"ACTOR_CODE","value":"123456"}'

# Stop an actor
curl -X POST http://localhost:8000/actors/wildberries_user_deterministic/stop

# Check health
python scripts/health.py
```

### 5. Local testing (optional)

```bash
# Run the extraction demo
mise run extract
```

---

## Setup

### Automatic setup (one-command pipeline)

```bash
bash setup.sh
```

### Manual setup (step-by-step)

1. Prepare environment: `mise run prepare`
2. Copy and fill environment: `cp .env.example .env` then edit with your credentials
3. Run desired pipeline: `mise run wb-analytics`

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /health | Server health check |
| GET | /actors | List all actors with state |
| GET | /actors/{name} | Actor details |
| POST | /actors/{name}/start | Spawn an actor |
| POST | /actors/{name}/stop | Stop an actor |
| POST | /actors/{name}/restart | Restart an actor |
| POST | /actors/{name}/pause | Pause an actor |
| POST | /actors/{name}/resume | Resume an actor |
| POST | /actors/{name}/input | Deliver out-of-band input (SMS code, 2FA) |
| POST | /tasks | Enqueue a new task |
| GET | /sessions | Query sessions (optional ?status= filter) |
| GET | /proxies | List proxy pool status |

---

## Project Structure

```
browser/        Playwright context, fingerprint injection, stealth JS
configs/        Per-actor JSON profiles, behavior YAML files, proxy list
cookies/        Saved browser storage state per actor (session persistence)
db/             SQLite logging (sessions, logs, tasks)
engine/         YAML action execution, human behavior, LLM fallback, ban detection
orc/            FastAPI server, task queue, task runner
swarm/          Actor lifecycle, state machine, proxy pool, profile loading
scripts/        Health check, extraction demo, wb-analytics pipeline, fingerprint audit
tests/          Unit and integration tests
```

---

## Actor State Machine

```
IDLE -> LOGIN -> NAVIGATE -> ACTION -> EXTRACT -> REPORT -> IDLE
```

---

## Anti-Detection

- Unique browser fingerprint per actor with per-session randomization (20+ JS overrides)
- Bezier curve mouse movements, log-normal delays, variable typing speed
- Overrides for navigator.webdriver, WebGL vendor/renderer, canvas noise, audio sample rate, font list, screen dimensions, timezone, locale, chrome object, $cdc_ cleanup, permissions, battery, visibility, userAgentData, getClientRects
- Cookie persistence via Playwright storage_state — skip login on repeat runs
- Proxy rotation on HTTP 403/429
- Max 1 concurrent session per website per proxy
- NO_PROXY env var for local debugging without proxy

---

## Lint

```bash
pip install ruff
mise run lint
```
