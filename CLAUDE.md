# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Overview

**exec_func_assist** is a Discord-based executive function assistant bot backed by the Claude API. It functions as a productivity prosthetic: proactive nudges, structured check-ins, task decomposition, and energy-aware suggestions throughout the day. The full spec is in `exec_function_assistant_spec_v0.3.md` — read it before implementing anything.

This is a **greenfield project**. No implementation exists yet. Follow `/kickoff` before beginning any phase.

---

## Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Language | Python 3.12 | Matches Ubuntu 24.10 on mbox |
| Bot framework | `discord.py` 2.x | Buttons, embeds, @mentions |
| Scheduler | `APScheduler` `AsyncIOScheduler` | Runs within the bot's asyncio event loop |
| LLM | Anthropic Python SDK | Sonnet default; Opus on demand via `<USE_OPUS>` tag |
| Joplin | `aiohttp` → `http://joplin:41184` | Joplin CLI in Docker; read-only in Phases 1–2 |
| Calendar | Google Calendar API v3 | OAuth2, `calendar.readonly`; consolidated via ICS subscription URLs |
| State | JSON files + `aiofiles` | **Not SQLite.** One file per domain: `state.json`, `interactions.json`, `memory.json` |
| Deployment | Docker Compose | Same `docker-compose.yml` for dev (MacBook) and prod (mbox, 192.168.178.24) |
| Timezone | `Europe/Berlin` | All scheduling and time logic |

**Services in docker-compose:** `bot` (built from `./Dockerfile`), `joplin` (Joplin CLI container).

**Dev workflow:** `docker compose up` on MacBook. Full stack runs locally — no mbox needed during development.

**Deploy to mbox:** `./deploy.sh` → SSH → `git pull && docker compose up -d --build`.

**Google OAuth2:** Run `setup_calendar.py` on MacBook (has browser), copy `secrets/google_token.json` to mbox before first prod deploy.

---

## Architectural Decisions (Non-Negotiable)

These are closed decisions from the spec and kickoff. Do not re-open without flagging explicitly. Full rationale in `DECISIONS.md`.

- **JSON files for state, not SQLite.**
- **`user_id` on every state record from day one.** Multi-user is Phase 3; structure must support it from the start.
- **Configuration is per-user, not global constants.**
- **Joplin is read-only in Phases 1 and 2.**
- **Google Calendar is the only calendar integration.** External calendars consolidated via ICS subscription URLs — no separate sync service (Keeper.sh was evaluated and rejected: strips event names).
- **No authentication layer in Phase 1.**
- **Bot initiates interactions; user does not need to prompt it.**
- **Nothing calls `datetime.now()` directly.** All time-dependent logic uses the `Clock` abstraction (C16) so debug/time-simulation works.
- **All HTTP I/O is async (`aiohttp`).** Never use `requests` inside a coroutine.
- **User's name is `Gabriell` (two l's), stored in `config.json` as `user_name`.**
- **Monthly Anthropic API spend cap: `$10` default, configurable as `monthly_cost_limit_usd`.**

---

## Bot Operating Modes

The bot has three modes (weekday):
1. **Morning Routine** — structured interview at `07:30`, up to 5 short follow-up questions
2. **Work Mode** — active `09:15`–`16:00`, assertive tone, task triage and decomposition
3. **Recovery Mode** — active from `20:30`, depleted-energy framing, 15-minute commitments, couch-compatible tasks prioritized

On **weekends**, only Recovery Mode applies (no morning routine, no work nudges). The bot is silent during the day unless the user initiates.

The `"off today"` command suppresses all proactive messages for the rest of the day (bedtime reminder still sent unless user says "full silence").

---

## Scheduled Events (Default Config)

| Event | Default Time |
|-------|-------------|
| Morning Routine | `07:30` |
| Morning retry (if no response) | `07:30 + 90 min` |
| Day Kick-off | `09:15` |
| Midday Check-in | `13:00` |
| Evening Check-in | `20:30` |
| End-of-Day Review | `22:30` |
| Bedtime Reminder | `23:00` |

Nudge cooldown: 45 minutes minimum between unsolicited nudges. Minimum calendar gap to trigger a proactive nudge: 30 minutes.

---

## Context Assembly

Before each LLM call in Executive Function Assistant mode, assemble:
- Current time, day of week, current mode
- Today's calendar (meetings + free windows)
- Active Joplin tasks (unchecked, sorted by inferred priority)
- Last 3–5 interaction history entries
- Estimated energy level
- Local task queue
- Morning routine state (completed / skipped / retried)

For general conversation, only the user's message is needed.

---

## Phased Delivery

- **Phase 1 (MVP):** Bot skeleton, Joplin + Calendar connectors, context assembly, Claude API integration, scheduled check-ins with Discord buttons, JSON state, weekend mode, "off today", missed morning retry.
- **Phase 2:** Calendar gap detection, deadline proximity alerts, energy-aware mode switching, nudge cooldown, task decomposition, follow-up loop, midday/end-of-day check-ins, structured memory.
- **Phase 3:** Interaction analytics, user task queue, Joplin write-back, multi-user support, adaptive learning.

Flag gaps between phases during implementation rather than silently filling them with assumptions.

---

## Tone Constraints

Tone is a first-class feature. The bot must:
- Never guilt, shame, or pressure
- Always offer an easy exit
- Default to 15-minute commitments
- Name the first concrete physical action (never abstract suggestions like "work on Project X")
- Match assertiveness to estimated energy level

Prompt engineering for Recovery Mode tone has the same priority as functional correctness.
