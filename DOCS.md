# Human Bot Swarm

## Concept

Система для автономного управления множеством бот-инстансов (акторов), каждый из которых имитирует отдельного пользователя. Боты выполняют вход в произвольные веб-сервисы, навигируют, извлекают данные, выполняют действия и генерируют ежедневные отчеты. Основной фокус — необнаруживаемость, отказоустойчивость и оркестрация роя.

System that autonomously manages multiple bot instances (actors), each impersonating a distinct human user. Bots log into arbitrary web services, navigate, extract data, perform actions, and generate daily reports. Emphasizes undetectability, error resilience, and swarm orchestration.

<!---->

## Architecture

```
.
├── main.py                    # CLI entry + FastAPI server start
├── requirements.txt           # pip deps
├── pyproject.toml             # project metadata + ruff config (lint/format)
├── mise.toml                  # tool versions (python, ruff) + task definitions
│
├── orc/
│   ├── __init__.py
│   ├── server.py              # FastAPI app, routes (run/stop/status/input)
│   └── task_queue.py          # asyncio.Queue, task dispatch
│
├── swarm/
│   ├── __init__.py
│   ├── manager.py             # proxy pool, actor lifecycle, rate limits
│   ├── actor.py               # asyncio task per actor, state machine
│   ├── profile.py             # ActorProfile dataclass from JSON
│   ├── proxy.py               # ProxyPool, load_proxies, ban management
│   └── state.py               # ActorState enum, transitions
│
├── browser/
│   ├── __init__.py
│   ├── context.py             # Playwright context creation + fingerprint + cookies
│   ├── fingerprint.py         # 20+ JS override snippets, variance generation
│   └── stealth.js             # Static mirror of the init script
│
├── engine/
│   ├── __init__.py
│   ├── actions.py             # Execute YAML steps (click, fill, scroll, wait)
│   ├── yaml_reader.py         # Parse behavior/*.yaml, variable substitution
│   ├── executor.py            # Step loop, retry, LLM fallback, ban listener
│   ├── llm_fallback.py        # OpenRouter SDK: remap, dismiss popup, captcha
│   ├── human.py               # Bezier mouse, log-normal delays, type jitter
│   └── ban.py                 # HTTP 403/429 response listener
│
├── db/
│   ├── __init__.py
│   ├── sqlite.py              # Schema init, insert log, insert session, queries
│   └── logger.py              # Logger class with step/screenshot/fail methods
│
├── scripts/
│   ├── health.py              # Check actor alive + server health
│   ├── extract_analytics.py   # Demo: open, login, screenshot, extract tables, LLM
│   ├── wb-analytics.py        # WB pipeline: boots server, spawns actor, handles SMS, dumps data
│   └── fingerprint_check.py   # Crawl browserleaks + FingerprintJS, LLM leak audit
│
├── configs/
│   ├── actors/
│   │   ├── wildberries_user_deterministic.json
│   │   ├── browserleaks_check.json
│   │   └── fingerprintjs_check.json
│   ├── proxies.json           # [{endpoint, provider, banned}]
│   └── behavior/
│       ├── wildberries.yaml
│       └── wildberries_loggedin.yaml   # Skip-login variant for cookie sessions
│
├── cookies/                   # Serialized browser storage state per actor (gitignored)
├── output/                    # Captured screenshots + pipeline .txt artifacts (gitignored)
├── index.test.html            # Test target page
├── DOCS.md
└── AGENTS.md
```

```
┌──────────────────────────────┐
│  Orchestrator                │  FastAPI control API, task dispatch
│  (asyncio + FastAPI)         │
│  ┌────────────┐              │
│  │ Task Queue │  asyncio.Queue per website/actor
│  └────────────┘              │
│         │ spawns             │
│  ┌──────▼─────────────────┐  │
│  │ Actor (asyncio task)   │  │
│  │ ┌────────────────────┐ │  │
│  │ │ Browser Manager    │ │  │  Playwright context + fingerprint + cookies
│  │ │ Action Engine      │ │  │  deterministic steps + LLM fallback
│  │ │ Error Handler      │ │  │  popup, timeout, layout change, ban
│  │ └────────────────────┘ │  │
│  └─────────────────────────┘  │
└──────────────────────────────┘
          │ logs + screenshots → output/
┌─────────▼────────┐
│  SQLite           │  session logs, step logs, errors
└───────────────────┘
```

### Data Flow (Wildberries example)

```
Operator runs mise run wb-analytics → uvicorn subprocess starts
     │
     ▼
POST /actors/wildberries_user_deterministic/start
     │
     ├── cookies/wildberries_user_deterministic.json exists?
     │   ├── YES → load wildberries_loggedin.yaml (skip login, go straight to analytics)
     │   └── NO  → load wildberries.yaml (full login via SMS)
     │
     ▼
Action Engine runs deterministic YAML steps
     │
     ├── step OK ──> log to SQLite, continue
     │
     ├── wait_input (SMS code) ──> asyncio.Event blocks until operator delivers via API
     │
     ├── selector missing ──> send DOM to OpenRouter LLM → remap → retry
     │
     ├── popup ──> try predefined dismiss → LLM popup analysis
     │
     ├── timeout ──> retry 3x with backoff → skip if persistent
     │
     └── captcha ──> heuristic + LLM confirm → pause, notify operator
```

<!---->

## Actors

| Actor | What it does |
|-------|-------------|
| `wildberries_user_deterministic` | Logs into seller.wildberries.ru, navigates to popular-search-queries analytics, extracts table. Cookies persistence for skip-login |
| `test_user_1` | Logs into localhost:8111 (test page), navigates Orders, screenshots, scrolls |
| `test_user_2` | Same as test_user_1 with different credentials/fingerprint profile |
| `browserleaks_check` | Visits browserleaks.com sub-pages (ip, javascript, webrtc, canvas, webgl, fonts, geo, features, etc.), collects fingerprint data for stealth audit |
| `fingerprintjs_check` | Visits FingerprintJS demo page, collects visitorId and component hashes |

### Running actors

- `python main.py` — starts the FastAPI server on port 8000, then control actors via REST API (POST /actors/{name}/start etc.)
- `mise run serve` — serves index.test.html on port 8111 for local testing
- `mise run extract` — standalone Playwright demo: opens localhost:8111, extracts table, sends to LLM (no swarm)
- `mise run wb-analytics` — full integration: boots server, spawns WB actor, prompts operator for SMS code (if no cookies), delivers via API, dumps extracted data from SQLite to .txt
- `mise run fingerprint-check` — runs fingerprint audit, sends aggregated data to LLM for leak analysis

<!---->

## Features

### Feature: Swarm Orchestration
- ID: F-001
- Status: Done
- Description
- - Central orchestrator manages multiple actor lifecycles
- - Reads task queue (asyncio.Queue) per website, per actor, per action
- - FastAPI control API: start, pause, stop, restart
- - Health monitoring script checks actor aliveness + cookie validity
- User Flow
- - Operator sends command via FastAPI
- - Orchestrator enqueues task in asyncio.Queue
- - Worker spawns actor as asyncio task
- - Health script polls each actor status endpoint
- Technical Notes
- - Python asyncio + FastAPI
- - Task queue via asyncio.Queue
- - Health: simple script pinging actor + verifying cookies persist
- Test Spec
- - Send start command for single actor
- - Verify actor enters RUNNING state
- - Verify health check returns alive
- - Send stop command and verify actor terminates

### Feature: Actor Identity Management
- ID: F-002
- Status: Done
- Description
- - Each actor maintains a unique digital identity
- - Runs as asyncio task (not subprocess/Docker)
- - Loads own profile from JSON config file (fingerprint, credentials, behavior schedule)
- - Implements state machine via Python Enum: IDLE → LOGIN → NAVIGATE → ACTION → EXTRACT → REPORT → IDLE
- User Flow
- - Actor JSON profile created in configs/actors/{name}.json
- - On task, actor loads profile and transitions through states
- - On failure, actor terminates — next run starts fresh
- Technical Notes
- - State machine via Python Enum (no external lib)
- - Profiles stored as JSON files
- - No session persistence for actor state (cookies stored separately on disk)
- Test Spec
- - Create actor profile JSON
- - Verify state machine enum transitions correctly
- - Verify actor terminates cleanly on failure

### Feature: Browser Fingerprinting
- ID: F-003
- Status: Done
- Description
- - Injects realistic browser fingerprint before page creation
- - 20+ JS overrides: navigator.webdriver (value descriptor), WebGL vendor/renderer + depth params, canvas noise, audio sample rate, fonts, timezone, language, screen, plugins, platform, chrome object (app/runtime/loadTimes/csi), $cdc_ cleanup, permissions (notifications), battery suppression, visibility/hidden/hasFocus, userAgentData with high-entropy brands, getClientRects sub-pixel rounding, hardwareConcurrency, deviceMemory, connection, bluetooth/usb/serial suppression, Sec-CH-UA header override
- - Uses realistic Windows user-agent (Chrome/Edge stable)
- - Per-actor stable seed for reproducible fingerprints across sessions
- User Flow
- - Actor profile loaded with fingerprint JSON
- - Fingerprint injected via page.add_init_script + CDP Page.addScriptToEvaluateOnNewDocument (double injection)
- - Variance applied per session from stable seed (canvas noise, audio jitter, font shuffle)
- Technical Notes
- - No playwright-stealth dependency — all overrides custom in fingerprint.py + stealth.js
- - Fingerprint profiles stored as JSON per actor
- - Tested via browserleaks.com + FingerprintJS automated audit
- Test Spec
- - Launch browser with fingerprint
- - Verify navigator.webdriver is undefined via page.evaluate
- - Verify fingerprint at browserleaks.com
- - Run fingerprint-check to detect leaks

### Feature: Action Engine
- ID: F-004
- Status: Done
- Description
- - Interprets action sequence from actor behavior file (YAML)
- - Deterministic execution with stable CSS/XPath selectors and retry
- - LLM fallback via OpenRouter SDK when selectors fail (remap, dismiss popup, captcha)
- - Behavioral emulation: random delays, mouse jitter, typing variance
- - Multi-element fill for SMS code inputs (6 individual fields)
- - Table extraction with polling (retries up to 60s until data cells appear)
- User Flow
- - Action Engine reads YAML steps: [type, selector, value?]
- - Executes deterministic steps (click, fill, wait, scroll, navigate, screenshot, extract_table)
- - On selector failure, sends DOM to OpenRouter LLM for remapping
- - Applies human-like behavior (Bezier mouse curves, log-normal delays)
- Technical Notes
- - Random delays 200-1500ms with log-normal distribution
- - Mouse jitter via Playwright mouse.move with Bezier curves
- - Typing speed variance per character
- - LLM via OpenRouter SDK: pip install openrouter, from openrouter import OpenRouter
- - Captcha detected → pause task, log, notify operator for manual solve
- - Popup handling: predefined dismiss selectors + LLM popup analysis (text-only, no vision)
- - wait_input step type for SMS/2FA codes delivered out-of-band via API
- - extract_table polls for non-empty cells up to 30 attempts (60s)
- Test Spec
- - Run action sequence on known page
- - Verify all deterministic steps execute with correct selectors
- - Inject layout change and verify OpenRouter LLM fallback triggers
- - Verify behavioral timing falls within expected distribution

### Feature: Error Recovery
- ID: F-005
- Status: Done
- Description
- - Timeout retry up to 3x with exponential backoff (1s/3s/9s)
- - Layout change fallback to OpenRouter LLM for selector suggestion
- - Auth failure resets cookies, re-login with 2FA retry (max 2 attempts)
- - Unexpected popups dismissed via predefined triggers or LLM analysis
- - On unrecoverable error → log to SQLite, mark session failed, exit
- - Ban detection (403/429) via response listener → proxy rotation
- User Flow
- - Step times out → retry 1s/3s/9s → if still fails → log and skip step, session failed
- - Selector not found → LLM returns new selector → retry → still failing → halt
- - Popup appears → check predefined dismiss → if unknown → LLM analysis → click
- - Actor crashes → no resume, next run starts from scratch
- Technical Notes
- - Exponential backoff: 1s, 3s, 9s
- - Inline try/except per step (no global popup listener)
- - Auth failure max 2 retry attempts
- - No session checkpoint — clean slate each run
- - Ban listener filtering: analytics API 403s (Facct) are logged but do not trigger proxy rotation
- Test Spec
- - Inject artificial timeout and verify retry logic
- - Inject popup and verify auto-dismiss
- - Inject layout change and verify OpenRouter LLM fallback

### Feature: Swarm Manager
- ID: F-006
- Status: Done
- Description
- - Maintains pool of proxy endpoints from JSON config
- - Assigns each actor unique proxy and browser context (round-robin, any available)
- - Limits concurrent sessions per website to avoid rate limits
- - Detects IP ban (403/429) and rotates proxy
- - NO_PROXY env var support: skip proxy pool entirely for local debugging
- User Flow
- - Swarm Manager reads configs/proxies.json for available proxies
- - On spawn, picks next available non-banned proxy and assigns to actor
- - Monitors concurrent session count per website
- - On ban detection, marks proxy as banned and rotates to next available
- Technical Notes
- - Proxy pool: simple JSON list
- - Max 1 concurrent session per website per IP
- - Random pause between task batches for rate limiting
- - Actors run as asyncio tasks (no Docker)
- Test Spec
- - Spawn multiple actors for same website
- - Verify session limit is enforced
- - Simulate 403 and verify proxy rotation

### Feature: Logging
- ID: F-007
- Status: Done
- Description
- - Structured log per step: actor, session, timestamp, status, message
- - Screenshots on failed steps
- - All logs in SQLite for simple querying
- User Flow
- - Each action step logged to SQLite
- - On error, screenshot captured and path stored in log
- - Raw logs available via SQL queries or log dump
- Technical Notes
- - Storage: single SQLite database
- - Screenshots saved to output/{session_id}/{step_name}.png
- - Timestamped run folders for pipeline output artifacts
- Test Spec
- - Run action sequence and verify SQLite log entries
- - Trigger error and verify screenshot file exists
- - Query logs by actor and verify correct ordering

<!---->

### Feature: Table Extraction + LLM Analytics
- ID: F-008
- Status: Done
- Description
- - Open a target page, log in, take screenshots, and extract structured data from HTML tables
- - Send extracted table rows to OpenRouter LLM with a fixed system-prompt and table data as the user-prompt
- - LLM returns analytics/messaging output based on the prompt (e.g. draft a supplier email from collected emails)
- User Flow
- - Actor opens index.test.html served by mise run serve (port 8111)
- - Fills login form, submits, navigates to Orders page
- - Screenshots captured at landing, dashboard, and Orders views
- - All table elements read via page.evaluate into row/cell text
- - Table text sent to OpenRouter as user message; system-prompt instructs the model
- - LLM response printed to stdout as the analytics result
- Technical Notes
- - Standalone demo script: scripts/extract_analytics.py (run via mise run extract)
- - Token and model read from env: OPENROUTER_TOKEN, OPENROUTER_MODEL (set in mise.toml)
- - Uses OpenRouter SDK chat.send with system + user messages
- - Screenshots saved to timestamped output/extract-* folder
- - No SQLite logging, no actor state machine — minimal end-to-end demonstration
- Test Spec
- - Start server: mise run serve
- - Run mise run extract and verify screenshots are created
- - Verify extracted table text is printed to stdout
- - Verify a non-empty LLM analytics response is printed

### Feature: Session Persistence (Cookies)
- ID: F-009
- Status: Done
- Description
- - Save browser storage state (cookies + localStorage) after successful login
- - Restore state on next run to skip SMS/2FA
- - Separate behavior YAML for logged-in flow (wildberries_loggedin.yaml)
- User Flow
- - First run: full login via SMS → cookies/wildberries_user_deterministic.json saved
- - Subsequent runs: cookies loaded via Playwright storage_state → actor goes straight to analytics
- - If cookies expire: YAML steps fail, table remains empty — operator deletes cookies and re-runs with SMS
- Technical Notes
- - Uses Playwright context.storage_state() — saves cookies + localStorage + sessionStorage
- - Cookies stored in cookies/{actor_name}.json
- - NO_PROXY env var: skip proxy pool, no proxy assigned to actor
- Test Spec
- - First run: verify cookies file created after successful login
- - Second run: verify actor completes without wait_input (SMS skipped)
- - Delete cookies file: verify SMS prompt returns

### Feature: Fingerprint Audit
- ID: F-010
- Status: Done
- Description
- - Automated crawl of browserleaks.com (all sub-pages) and FingerprintJS demo
- - Collect full fingerprint surface (navigator, screen, chrome, webgl, canvas, audio, timezone, permissions, userAgentData, visibility, battery, cdc_, clientRects, webrtc)
- - Send aggregated data to OpenRouter LLM for leak analysis
- User Flow
- - Run mise run fingerprint-check
- - Script launches Playwright with stealth overrides, visits browserleaks.com sub-pages
- - For each sub-page: wait for JS tests to complete, screenshot, extract page data
- - Visit fingerprintjs.github.io, collect visitorId + component hashes
- - Aggregate all data into single JSON, send to LLM for leak analysis
- - Save LLM output to fingerprint_audit_llm_output.txt
- Technical Notes
- - Standalone script: scripts/fingerprint_check.py
- - 15+ browserleaks sub-pages crawled (ip, javascript, webrtc, canvas, webgl, fonts, geo, features, tls, proxy, client-hints, rects, chrome, dns)
- - Separarate OpenRouter model recommended for analysis (FINGERPRINT_LLM_MODEL env var)
- - Output saved to timestamped output/fingerprint-* folder
- Test Spec
- - Run mise run fingerprint-check
- - Verify output folder contains JSON files per sub-page + LLM analysis

### Feature: WB Analytics Pipeline
- ID: F-011
- Status: Done
- Description
- - End-to-end pipeline: boot FastAPI server, spawn WB actor, handle SMS, extract analytics table, save to .txt
- - Cookie-aware: skips login on subsequent runs
- - Single actor (wildberries_user_deterministic) with deterministic selectors
- User Flow
- - mise run wb-analytics starts the pipeline
- - Uvicorn subprocess started with stealth fingerprints
- - Actor checks for cookies file → if exists: loads wildberries_loggedin.yaml (no SMS)
- - If no cookies: loads wildberries.yaml (full login), operator enters SMS code via stdin
- - Table extraction polls for data cells up to 60s
- - Extracted data saved to output/wb-*/wb_analytics_wildberries_user_deterministic.txt
- Technical Notes
- - Single actor, remove wildberries_user_llm (resolve_mode="llm" removed)
- - SQLite log query uses ORDER BY DESC for latest session
- - Data saved immediately after each actor completes (not waiting for others)
- - Session logs printed every 30s during wait
- Test Spec
- - First run: verify SMS prompt → enter code → verify .txt with analytics data
- - Second run: verify SMS skipped (cookies) → verify .txt with fresh data

<!---->

## Technology Stack

| Layer              | Technology                          | Rationale                                      |
|--------------------|-------------------------------------|------------------------------------------------|
| Language           | Python 3.12+                        | Async, rich AI libs, Playwright support        |
| Browser Automation | Playwright                          | Better fingerprint control than Selenium       |
| Anti-Detection     | Custom JS (fingerprint.py + stealth.js) | 20+ targeted overrides, no 3rd party libs   |
| LLM                | OpenRouter SDK                      | Single SDK for 400+ models, type-safe, async   |
| Task Queue         | asyncio.Queue                       | Zero deps, in-process, no external services    |
| Proxy Management   | JSON config + custom pool           | Lightweight, no external service               |
| Session Persistence| Playwright storage_state            | Cookies + localStorage serialization to disk   |
| Actor Configs      | JSON files                          | Simple, version-controllable                   |
| Logging            | SQLite                              | Zero setup, single file, queryable             |
| State Machine      | Python Enum                         | No external dependency                         |
| Control API        | FastAPI                             | Async, auto-docs, lightweight                  |
| Lint/Format        | Ruff                                | Fast Python linter + formatter, single tool    |
| Tool Manager       | mise                                | Pins python + ruff versions, runs tasks        |

### Ports

| Service        | Port   | Protocol |
|----------------|--------|----------|
| FastAPI Control| 8000   | HTTP     |
| Test Page      | 8111   | HTTP     |

### Commands

| Command              | Description                          |
|----------------------|--------------------------------------|
| `python main.py`     | Start FastAPI server on port 8000    |
| `mise run lint`      | Run ruff lint check + format check   |
| `mise run prepare`   | Install Python deps + Playwright browsers |
| `mise run serve`     | Serve index.test.html on port 8111   |
| `mise run extract`   | Open page, log in, screenshot, extract tables, run LLM analytics |
| `mise run wb-analytics` | WB pipeline: server + actor + SMS + table dump |
| `mise run fingerprint-check` | Crawl browserleaks + FingerprintJS, LLM leak audit |
| `python scripts/health.py` | Check server /health endpoint |

<!---->

## Database Schema (SQLite)

### logs
| Column       | Type     | Description                        |
|------------- |----------|------------------------------------|
| id           | INTEGER  | Primary key (auto-increment)       |
| session_id   | TEXT     | Session UUID                       |
| actor_id     | TEXT     | Actor name                         |
| step_name    | TEXT     | Step identifier                    |
| level        | TEXT     | info, warn, error                  |
| message      | TEXT     | Log message                        |
| screenshot   | TEXT     | Path to screenshot (nullable)      |
| created_at   | TEXT     | ISO 8601 timestamp                 |

### sessions
| Column       | Type     | Description                        |
|------------- |----------|------------------------------------|
| id           | TEXT     | UUID (primary key)                 |
| actor_id     | TEXT     | Actor name                         |
| website      | TEXT     | Target website                     |
| status       | TEXT     | running, success, failed           |
| errors       | INTEGER  | Error count                        |
| started_at   | TEXT     | ISO 8601 timestamp                 |
| finished_at  | TEXT     | ISO 8601 timestamp (nullable)      |

### tasks
| Column       | Type     | Description                        |
|------------- |----------|------------------------------------|
| id           | TEXT     | UUID (primary key)                 |
| actor_id     | TEXT     | Actor name                         |
| website      | TEXT     | Target website                     |
| action       | TEXT     | JSON action sequence               |
| status       | TEXT     | pending, running, done, failed     |
| created_at   | TEXT     | ISO 8601 timestamp                 |

<!---->

## LLM vs Deterministic Logic

| Scenario                     | Approach         | Why                                           |
|------------------------------|------------------|-----------------------------------------------|
| Click, fill, scroll, wait    | Deterministic    | CSS/XPath selectors are reliable, fast, free  |
| Page navigation              | Deterministic    | Stable URL patterns                           |
| Layout change, missing selector | OpenRouter LLM | DOM sent, LLM returns new selector (remap) |
| Unknown popup                | OpenRouter LLM   | Predefined dismiss → LLM text analysis → click |
| Captcha confirmation         | Heuristic + LLM  | Keyword check → LLM confirm → pause or continue |
| 2FA / SMS code entry         | Deterministic    | Fixed selectors for code input fields         |
| Table extraction             | Deterministic    | JS querySelectorAll, polls for data up to 60s |
| Fingerprint generation       | Deterministic    | Pre-generated JSON, no LLM needed             |

LLM is used only as fallback — reactive remap on selector miss, popup dismissal, captcha confirmation. Proactive LLM resolve mode (resolve_mode="llm") was removed to simplify the architecture; all actors now use deterministic selectors with LLM fallback.

<!---->

## Anti-Detection Measures

| Measure             | Implementation                                      |
|---------------------|------------------------------------------------------|
| Fingerprint         | Unique profile per actor, 20+ JS overrides, per-actor stable seed |
| Behavioral          | Random delays 200-1500ms, Bezier mouse traces, variable typing speed |
| WebDriver detection | Override navigator.webdriver to undefined (value descriptor, not getter) |
| Session isolation   | Separate Playwright context per actor (different IP, cookies, storage) |
| CDP injection       | Double injection: context.add_init_script + Page.addScriptToEvaluateOnNewDocument |
| User-agent          | Realistic Windows Chrome/Edge, updated with browser versions |
| Client Hints        | Sec-CH-UA override (no HeadlessChrome), userAgentData with high-entropy brands |
| Chrome object       | Full window.chrome with app, runtime, loadTimes, csi |
| $cdc_ cleanup       | Strip CDP internal variables from window/document/navigator |
| Captcha             | Detect → confirm via LLM → pause task → notify operator for manual solve |
| Rate limits         | Max 1 concurrent session per website per IP, random pause between task batches |
| Proxy rotation      | On 403/429 → mark banned, pick next proxy from pool (analytics API 403s filtered) |
| Session persistence | Cookies saved to disk via Playwright storage_state — skip login on repeat runs |

<!---->

## Error Handling Matrix

| Error               | Detection                         | Action                                           |
|---------------------|-----------------------------------|--------------------------------------------------|
| Timeout             | page.wait_for_selector timeout    | Retry 3x (1s/3s/9s), then skip step, fail session|
| Layout change       | Selector not found                | Send DOM to OpenRouter LLM, apply new selector   |
| Auth failure        | Login page after expected URL     | Clear cookies, re-login, max 2 attempts          |
| Unknown popup       | DOM has unexpected overlay        | Predefined dismiss → LLM analysis → click        |
| Captcha             | Known captcha element detected    | Heuristic → LLM confirm → pause, notify operator |
| 403/429 (IP ban)    | HTTP status code                  | Mark proxy banned, rotate, retry. Analytics API 403s filtered out |
| Crash               | Task exception                    | Log error, mark session failed, exit             |

<!---->

## Configuration Structure

```
configs/
├── actors/
│   ├── wildberries_user_deterministic.json
│   ├── browserleaks_check.json
│   ├── fingerprintjs_check.json
│   ├── test_user_1.json
│   └── test_user_2.json
├── proxies.json           # list of {endpoint, provider, banned}
└── behavior/
    ├── wildberries.yaml           # Full login + analytics
    └── wildberries_loggedin.yaml  # Analytics-only (skip login via cookies)
```

### Actor JSON example

```json
{
  "name": "wildberries_user_deterministic",
  "fingerprint": {
    "screen": {"width": 1920, "height": 1080, "offset_x": 0, "offset_y": 0},
    "webgl_vendor": "Intel Inc.",
    "webgl_renderer": "Intel Iris OpenGL Engine",
    "fonts": ["Arial", "Calibri", "Segoe UI", "Times New Roman"],
    "audio_sample_rate": 44100,
    "timezone": "Europe/Moscow",
    "language": "ru-RU",
    "languages": ["ru-RU", "en-US", "en"],
    "platform": "Win32",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
  },
  "credentials": {
    "url": "https://seller.wildberries.ru",
    "login_env": "WILBERRIES_USER_DETERMINISTIC_PHONE"
  },
  "behavior": "configs/behavior/wildberries.yaml"
}
```

<!---->

## Health Monitoring

Simple script that:
1. Hits FastAPI /health endpoint
2. For each active actor, checks:
   - Process/task is alive
   - Browser context is responsive
   - Cookies are still valid (quick page load check)
3. Logs status to SQLite
4. Reports dead actors and expired cookies

Run via cron or systemd timer every 60s.

<!---->

## Testing Strategy

- **Unit tests**: Actor config loading, fingerprint injection, state machine transitions, error handling, proxy pool
- **Integration**: Run against staging website mimicking target (e.g., mock Wildberries)
- **Resilience**: Inject artificial popups, timeouts, layout changes; verify agent recovers or fails gracefully
- **Anti-detection**: Verify fingerprint at browserleaks.com via fingerprint-check; test headless/headed; check no detection by Facct

<!---->

## Deployment

- Single Python process (asyncio) — no external services required
- SQLite database in project root
- Environment variables for secrets: OPENROUTER_TOKEN, OPENROUTER_MODEL, actor phone env vars
- Cookies directory for session persistence
- Health monitoring via cron/systemd

<!---->

## Future Ideas

- Web-based control dashboard
- A/B testing of fingerprint profiles
- CAPTCHA solver service integration
- Multi-session parallel analytics extraction
- Export reports to Google Sheets / Telegram
- Automated proxy rotation from free proxy lists (scripts/update_proxies.sh)
