# Project Overview

Human Bot Swarm — autonomous multi-actor web automation with anti-detection.

## Stack
- Python 3.11+, Playwright, FastAPI, OpenRouter SDK, SQLite
- No external services (Redis, Postgres, Elasticsearch) — single process
- Fingerprint injection via `page.add_init_script()` + CDP session (JS overrides for webdriver, WebGL, canvas, audio, fonts, $cdc_, window.chrome, permissions, battery, visibility, etc.)

## Project Structure
- `orc/` — FastAPI server, task queue (asyncio.Queue)
- `swarm/` — proxy pool, actor lifecycle, state machine (Python Enum)
- `browser/` — Playwright context, fingerprint injection, stealth JS
- `engine/` — YAML action execution, LLM fallback (remap, dismiss popup, captcha)
- `db/` — SQLite logging
- `configs/actors/` — per-actor JSON configs (fingerprint, credentials, behavior ref)
- `configs/behavior/` — YAML action step files
- `cookies/` — serialized browser storage state per actor (session persistence)
- `scripts/` — health check, extract analytics, wb-analytics pipeline, fingerprint audit

## Commands
- `python main.py` — start FastAPI server
- `mise run lint` — run ruff lint + format check
- `python scripts/health.py` — check actor status
- `mise run wb-analytics` — run the WB analytics pipeline
- `mise run fingerprint-check` — audit fingerprint leaks via browserleaks + FingerprintJS + LLM

## Coding Rules
- No external state machine libs (use Python Enum)
- No Docker — actors run as asyncio tasks
- No session persistence — restart from scratch on failure (cookies stored separately on disk)
- LLM used as fallback only (remap on selector miss, dismiss popup, confirm captcha)
- All logs to SQLite

<!---->

## Documentation Style

Project docs follow the `DOCS.md` convention at repo root.

### Structure
- `# Title` — H1 with project name
- `## Concept` — Bilingual overview (Russian Концепт + English Project Overview)
- `## Architecture` — Frontend/backend/data-flow with ASCII diagrams
- `## Features` — Structured feature list
- `## API Reference` — Endpoints grouped by controller
- `## Database Schema` — Table descriptions
- `## State Management` — Stores and storage keys

### Feature entry format
```markdown
### Feature: Title
- ID: F-XXX
- Status: Done | Planned
- Description
- - Item
- User Flow
- - Item
- Technical Notes
- - Item
- Test Spec
- - Navigate to /
- - Verify result
```

### Conventions
- English headings, Russian concept section optional
- Feature IDs sequential: F-001, F-002...
- ASCII diagrams for pipeline/data flows
- Tables for structured data (ports, commands, endpoints)
- HTML comments (`<!---->`) as separators
- Future ideas as unordered list at bottom


## Agent rules for TODO.md

### Purpose

Track project-wide tasks and epic plans in `TODO.md` at repo root.

### Epic structure

- Keyword-based epics: `## epic-keyword | Description` (e.g. `## epic-auth | NestJS Core Authentication and Multi-tenancy`)
- Each epic has 4 to 15 items depending on scope
- Epics are independent, no overlap
- Do not work on an epic unless asked
- Do not modify tasks or epics unless explicitly requested

### Style guide for TODO.md

- Heading levels allowed: `#`, `##`, `###` only
- Section titles: `#`
- Epics: `##`
- No bold, italics, code ticks, links, or other markdown
- Lists: only `-` (hyphen) items
- No `- [ ]` or `- [x]` checkboxes
- Done items: insert `[DONE]` immediately after the prefix
  - Examples: `- [DONE] text`, `- - [DONE] text`
- Do not change original item text except to add `[DONE]`
- Do not add `[DONE]` to titles (`#`, `##`, `###`)

### Important moments

- Do not change linter settings
- Do not change how project commands run
- Do not build project – only run lint
- Run all commands with `timeout`
- If user mentions `TODO.md` and work involves its tasks/epics, update `TODO.md` to reflect current state
