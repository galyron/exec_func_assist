# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Overview

**exec_func_assist** (EVA) is a Discord-based executive function assistant bot backed by the Claude API. It sends proactive structured check-ins, task suggestions, and energy-aware nudges throughout the day. The full spec is in `exec_function_assistant_spec_v0.3.md`. Architecture decisions are in `DECISIONS.md`. The phased build plan is in `PLAN.md`.

**Implementation status:** Phases 1-A through 1-E are complete. The bot is fully operational: connectors, context assembler, LLM client, all scheduled handlers (C8–C11, C14), on-demand routing (C12), and follow-up scheduling (C13) are all working.

**Remaining gaps before Phase 2:**
- Joplin + Calendar background polling jobs are described in C14 but not yet added to `scheduler.py` (connectors are currently called on-demand per LLM request only).
- C17 (Cost Tracker): spend tracking and cap enforcement are in `llm/client.py`, but Discord warning messages at 80% and 100% cap are not yet sent.
- `deploy.sh` is referenced in the plan but may not exist yet.
- Debug mode enhancements planned (print LLM payloads, suppress @mentions) are not implemented.

---

## Common Commands

**Run all tests:**
```sh
python -m pytest tests/ -q
```

**Run a single test file:**
```sh
python -m pytest tests/connectors/test_joplin.py -q
```

**Run a single test by name:**
```sh
python -m pytest tests/context/test_assembler.py -k "test_mode_weekend" -q
```

**Start the full stack (dev):**
```sh
docker compose up
```

**Rebuild and restart:**
```sh
docker compose up --build
```

**Verify connectors (stack must be running):**
```sh
docker compose run --rm bot python -m connectors.joplin
docker compose run --rm bot python -m connectors.calendar
```

**Verify context assembly + LLM end-to-end:**
```sh
docker compose run --rm bot python -m context.assembler
docker compose run --rm bot python -m llm.client
```

**Debug mode (time simulation):**
```sh
docker compose run --rm bot python bot.py --debug --debug-time "2026-03-24 07:25" --debug-multiplier 120
```

**One-time Google Calendar OAuth setup (run on MacBook, needs browser):**
```sh
python setup_calendar.py
```

---

## Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Language | Python 3.12 | Matches Ubuntu 24.10 on mbox |
| Bot framework | `discord.py` 2.x | Buttons, embeds, @mentions |
| Scheduler | `APScheduler` `AsyncIOScheduler` | Runs within the bot's asyncio event loop |
| LLM | Anthropic Python SDK | Sonnet default (`claude-sonnet-4-6`); Opus on demand |
| Joplin | `aiohttp` → `http://joplin:41184` | Joplin CLI in Docker; read-only in Phases 1–2 |
| Calendar | Google Calendar API v3 | OAuth2, `calendar.readonly` |
| State | JSON files + `aiofiles` | **Not SQLite.** `state.json`, `interactions.json`, `memory.json` |
| Deployment | Docker Compose | Same `docker-compose.yml` for dev (MacBook) and prod (mbox) |
| Timezone | `Europe/Berlin` | All scheduling and time logic |

**Services:** `eva-bot-dev` (bot container), `eva-joplin-dev` (Joplin CLI + socat forwarder).

**Joplin network note:** Joplin's REST API only binds to `127.0.0.1` inside its container. The entrypoint runs Joplin on internal port 41185 and uses `socat` to forward `0.0.0.0:41184 → 127.0.0.1:41185` so the bot can reach it via Docker DNS (`http://joplin:41184`).

**Prod deploy:** `./deploy.sh` → SSH to mbox (192.168.178.24) → `git pull && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`.

---

## Architecture

### Data Flow

```
Discord message ──▶ EFABot._handle_message()
                         │
                         ├─ MorningRoutineHandler (C8)  [if active]
                         └─ OnDemandHandler (C12)       [all other messages]
                                  │
                                  ├─ (LLM intents) ──▶ ContextAssembler (C5)
                                  │                          │
                                  │                     JoplinConnector (C3)
                                  │                     CalendarConnector (C4)
                                  │                     StateManager (C2)
                                  │                          │
                                  │                     LLMClient (C6) ──▶ Discord reply
                                  │
                                  └─ FollowupHandler (C13) ──▶ APScheduler date job

APScheduler ──▶ Scheduler (C14) fires:
  MorningRoutineHandler / KickoffHandler / CheckinHandler / BedtimeHandler
```

Every LLM call: fetch tasks + events + recent interactions → assemble context string → send to Claude → post response to Discord.

### Key Modules

**`config.py` (C1)** — Frozen `Config` dataclass. Loads secrets from `.env`, settings from `config.json`. Raises `ConfigError` on missing values. `config.json` is committed (no secrets); `.env` is gitignored. Notable optional field: `security_alerts_channel_id` (Discord channel for unauthorized-message alerts; `null` = log-only).

**`state/manager.py` (C2)** — `StateManager`: async read/write for three JSON files. Writes are atomic (`.tmp` → rename). Handles daily rollover (archives `daily` → `previous_daily` on date change). Key methods: `get_daily()`, `update_daily(**kwargs)`, `append_interaction()`, `get_recent_interactions(n)`, `has_previous_daily()`.

**`connectors/models.py`** — Shared output types: `Task`, `CalendarEvent`, `FreeWindow`. These are the contract between connectors and `ContextAssembler`. New calendar sources only need to produce these types.

**`connectors/joplin.py` (C3)** — Reads all notes via Joplin REST API with pagination. Two task sources: standalone todo notes (`is_todo=1`) and unchecked checklist items in regular note bodies. Extracts inline tags `[high]`, `[low-energy]`, `[couch]`, `[easy]`. Returns `[]` on failure (graceful degradation).

**`connectors/calendar.py` (C4)** — Enumerates all selected calendars via `calendarList.list` (not just `primary`). Fetches events per calendar. Pure function `compute_free_windows()` computes free time slots. Excluded calendars configured via `excluded_calendar_ids` in `config.json`.

**`context/assembler.py` (C5)** — Pure functions `determine_mode()` and `determine_energy()` are module-level (testable without class). `ContextAssembler.assemble()` takes pre-fetched data and returns `AssembledContext` with a formatted `text` field ready for the LLM.

**`llm/client.py` (C6)** — `LLMClient.send()` selects Sonnet/Opus based on session state, prepends the context string to the user message, tracks monthly spend in `state.json`, enforces `monthly_cost_limit_usd`. Opus auto-reverts after `opus_session_max_messages`.

**`llm/prompts.py`** — System prompts keyed by `Mode` enum. Tone constraints are encoded here; this is a first-class feature, not an afterthought.

**`utils/clock.py` (C16)** — `Clock` abstraction. `RealClock` for production; `DebugClock` for time-simulation (configurable multiplier). **Nothing calls `datetime.now()` directly** — always use `clock.now()`.

**`bot.py` (C7)** — `EFABot(discord.Client)`. Both channel and DM messages enter `_handle_message()`. Morning routine takes priority when active; all other messages route through `OnDemandHandler`. `_build_bot()` factory wires all handlers; `on_ready()` injects APScheduler into `FollowupHandler` after the scheduler starts (avoids circular dependency). `on_message` enforces `discord_user_id` — all other authors are silently dropped and optionally reported to `security_alerts_channel_id` via `_alert_unauthorized()`.

**`handlers/base.py`** — `BaseHandler` superclass. Provides `_log_bot(msg)` and `_log_user(msg)` for interaction logging, plus the `SendFn` type alias. All handlers extend this.

**`handlers/morning.py` (C8)** — Stateful multi-turn morning interview. `fire()` / `fire_retry()` for scheduled triggers; `handle_response()` for user replies; `is_active()` to check routing priority.

**`handlers/kickoff.py` (C9)** — Sends the LLM-generated day briefing at `work_start`.

**`handlers/checkin.py` (C10)** — Parameterised by `CheckinType` (MIDDAY / EVENING). `fire(type, send_fn)` sends LLM message + `_CheckinView` buttons. `handle_text_response()` accepts typed equivalents.

**`handlers/bedtime.py` (C11)** — `fire_end_of_day()` generates an LLM micro-review from `interactions.json` (skipped if `off_today`). `fire_bedtime()` sends a fixed message (only skipped if `off_today_full_silence`).

**`handlers/on_demand.py` (C12)** — Module-level `detect_intent(text) -> Intent` pure function (testable without class). `OnDemandHandler.handle()` dispatches to sub-handlers for each intent: OFF_TODAY, FINISHED, STUCK, SKIP, ADD_TASK, USE_OPUS, GENERAL. STUCK calls LLM then schedules follow-up; FINISHED cancels it.

**`handlers/followup.py` (C13)** — `FollowupHandler` schedules a one-shot APScheduler `date` job 20 min after a suggestion. `set_apscheduler()` injects the scheduler post-construction. `cancel()` silently tolerates missing job or absent scheduler. Sends `_FollowupView` buttons (Done / Still working / Skipped).

### Mode Determination (weekdays)

| Time | Mode |
|------|------|
| Before `work_start` (09:15) | `MORNING` |
| `work_start` → `work_end` (16:00) | `WORK` |
| `work_end` → `evening_start` (20:30) | `GENERAL` |
| After `evening_start` | `RECOVERY` |
| Saturday / Sunday | `WEEKEND` |

### Energy Heuristic

Declared energy (from morning routine) always overrides. Default:
- `RECOVERY` or `WEEKEND` → `low`
- Within ±60 min of `midday_checkin` (13:00) → `medium-low`
- Otherwise → `medium`

---

## Non-Negotiable Architectural Decisions

Full rationale in `DECISIONS.md`. Do not re-open without flagging explicitly.

- **JSON state, not SQLite.**
- **`user_id` on every state record.** Multi-user is Phase 3; structure supports it from day one.
- **Clock abstraction is mandatory.** Never call `datetime.now()` directly anywhere.
- **All HTTP I/O is async (`aiohttp`).** Never use `requests` inside a coroutine.
- **Joplin is read-only in Phases 1 and 2.**
- **Google Calendar only.** External calendars imported via ICS subscription URLs in Google Calendar — no separate sync service.
- **User's name is `Gabriell` (two l's)** — stored in `config.json` as `user_name`.
- **Monthly Anthropic API spend cap: `$10` default**, configurable as `monthly_cost_limit_usd`.
- **`secrets/` is gitignored.** Contains `google_token.json`, `google_client_secret.json`, and `pre_implementation_checklist.md`. `config.json` is committed (no secrets).

---

## Bot Behaviour

**Weekday modes:**
1. **Morning** (07:30) — structured interview, one question at a time, max 5 follow-ups
2. **Work** (09:15–16:00) — assertive, concrete first actions, task triage
3. **Recovery** (20:30+) — low-pressure, couch-compatible tasks, 15-min max commitments

**Weekends:** silent unless user initiates. Evening nudge configurable via `weekend_evening_nudge`.

**`"off today"`** suppresses all proactive messages for the day (bedtime reminder still fires unless `"full silence"`).

**Nudge cooldown:** 45 min minimum between unsolicited messages. Calendar gap must be ≥ 30 min to trigger a nudge.

**Tone (first-class feature):** never guilt/shame/pressure; always offer an easy exit; name the first physical action; default to 15-min commitments.

---

## Testing

Tests use `pytest-asyncio`. Async test functions work without `@pytest.mark.asyncio` — check `pytest.ini` or `pyproject.toml` for `asyncio_mode = auto`.

Connectors are always mocked in unit tests — no live API calls. `compute_free_windows()` and `determine_mode()`/`determine_energy()` are pure functions tested exhaustively without mocks.

The `conftest.py` in `tests/` sets up shared fixtures.
