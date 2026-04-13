# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Overview

**exec_func_assist** (EVA) is a Discord-based executive function assistant bot backed by the Claude API. It sends proactive structured check-ins, task suggestions, and energy-aware nudges throughout the day. The full spec is in `exec_function_assistant_spec_v0.3.md`. Architecture decisions are in `DECISIONS.md`. The phased build plan is in `PLAN.md`.

**Implementation status:** Phases 1 and 2-B are complete. The bot is fully operational: connectors (including Joplin write-back and Calendar event creation), context assembler, LLM client (single-turn), all scheduled handlers (C8–C11, C14), on-demand routing (C12), follow-up scheduling (C13), commitment timers, timed reminders (C15), and periodic nudges (C14-N) are all working.

**Remaining gaps:**
- C17 (Cost Tracker): spend tracking and cap enforcement are in `llm/client.py`, but Discord warning messages at 80% and 100% cap are not yet sent.
- Debug mode enhancements planned (print LLM payloads, suppress @mentions) are not implemented.

---

## Common Commands

```sh
# Run all tests
python -m pytest tests/ -q

# Run a single test file
python -m pytest tests/connectors/test_joplin.py -q

# Run a single test by name
python -m pytest tests/context/test_assembler.py -k "test_mode_weekend" -q

# Start the full stack (dev)
docker compose up

# Rebuild and restart
docker compose up --build

# Verify connectors (stack must be running)
docker compose run --rm bot python -m connectors.joplin
docker compose run --rm bot python -m connectors.calendar

# Verify context assembly + LLM end-to-end
docker compose run --rm bot python -m context.assembler
docker compose run --rm bot python -m llm.client

# Debug mode (time simulation — 120x = 1 real second ≈ 2 simulated minutes)
docker compose run --rm bot python bot.py --debug --debug-time "2026-03-24 07:25" --debug-multiplier 120

# One-time Google Calendar OAuth setup (run on MacBook, needs browser)
python setup_calendar.py

# Deploy to mbox (commit + push first, then ./deploy.sh pulls on the server)
./deploy.sh
```

---

## Stack

Python 3.12 · `discord.py` 2.x · APScheduler `AsyncIOScheduler` · Anthropic Python SDK (Sonnet default, Opus on demand) · `aiohttp` for Joplin REST API · Google Calendar API v3 · JSON state files + `aiofiles` · Docker Compose · Timezone: `Europe/Berlin`.

Two Docker services: `eva-bot-dev` (bot) and `eva-joplin-dev` (Joplin CLI + socat forwarder). Joplin binds to `127.0.0.1` inside its container; `socat` forwards `0.0.0.0:41184 → 127.0.0.1:41185` so the bot reaches it via Docker DNS at `http://joplin:41184`.

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
                                  ├─ FollowupHandler (C13) ──▶ APScheduler date job
                                  └─ ReminderHandler (C15) ──▶ APScheduler date jobs (multiple)

APScheduler ──▶ Scheduler (C14) fires:
  MorningRoutineHandler / KickoffHandler / CheckinHandler / BedtimeHandler / NudgeHandler
```

Every LLM call: fetch tasks + events + recent interactions → assemble context string (including factual exchange log) → single-turn send to Claude → post response to Discord. Interactions are summarised as structured facts in the context string, NOT passed as separate API turns — this prevents tone contamination from prior soft-mode responses.

### Critical Wiring Pattern

Adding a new handler requires changes in multiple files due to deliberate circular-dependency avoidance:

1. **Create the handler** in `handlers/` extending `BaseHandler`.
2. **Instantiate it** in `bot.py` → `_build_bot()`. If it needs `channel.send`, pass `get_send_fn=lambda: None` initially.
3. **Wire the send function** after `EFABot` is constructed: `handler._get_send_fn = bot._get_channel_send`.
4. **If it uses APScheduler**, call `handler.set_apscheduler(self._scheduler._scheduler)` in `EFABot.on_ready()` (the scheduler isn't available until then).
5. **If it has a cron job**, register it in `scheduler.py` → `_register_jobs()`. Remember: **every `CronTrigger` must have `timezone=tz` explicitly** — the scheduler's timezone does NOT propagate to triggers; omitting it causes jobs to fire on UTC.
6. **If it has an intent**, add it to `on_demand.py` → `Intent` enum, `detect_intent()`, and `handle()` dispatch.

### Key Modules — What's Non-Obvious

**`bot.py`** — `on_message` has three branches in strict order: (1) monitor-only channels from `config.monitor_channels` — security check with per-channel allowlist (owner is always implicitly authorized), **never routed to LLM**; (2) authorization check against `discord_user_id` — unknown authors trigger `_alert_unauthorized()` which posts to `security_alerts_channel_id`; (3) DM or `discord_channel_id` filter. Discord bots receive every message in every readable channel — filtering happens at the application layer, so **ordering matters**. Reordering these branches changes who gets alerted where.

**`config.py` (C1)** — Frozen `Config` dataclass. `config.json` is committed (no secrets); `.env` is gitignored.

**`state/manager.py` (C2)** — Atomic writes (`.tmp` → rename). Daily rollover happens automatically on every `get_daily()` / `update_daily()` call when the clock date changes.

**`connectors/calendar.py` (C4)** — `last_fetch_failed` flag is set after each `get_events()` call. Passed through to the context assembler so the LLM context warns "CALENDAR UNAVAILABLE" instead of silently showing empty. Silent failures were a real production issue — the LLM gave bad time-window advice when it thought the calendar was empty.

**`context/assembler.py` (C5)** — `determine_mode()` and `determine_energy()` are module-level pure functions (testable without class). `assemble()` takes pre-fetched data + `calendar_failed` flag. Events are labelled `[past]`, `[now]`, or `[upcoming]` relative to `clock.now()`.

**`llm/client.py` (C6)** — **Single-turn model:** each call sends one `user` message with full context in the system prompt. No multi-turn API history. This is deliberate — prevents tone contamination across mode transitions.

**`llm/prompts.py`** — System prompts keyed by `Mode` enum. **Hardcoded trigger strings inside each handler (`fire()`, `fire_end_of_day()`, etc.) are equally load-bearing** — they are the user-turn instruction that shapes the LLM output. If you change tone, update both `prompts.py` AND the trigger strings in the relevant handler. The end-of-day trigger includes `clock.now()` date+time explicitly to prevent day-of-week hallucination.

**`utils/clock.py` (C16)** — `Clock` abstraction. `RealClock` for production; `DebugClock` for time-simulation. **Nothing calls `datetime.now()` directly** — always use `clock.now()`.

**`handlers/on_demand.py` (C12)** — `detect_intent()` is a pure function with **order-sensitive matching**. Critical ordering: REMINDER ("`remind me at 14:30`") must be checked BEFORE COMMIT ("`remind me in 30 min`") — both start with "remind me" but mean different things. FINISHED and STUCK use `re.match` (start-of-message only) to avoid false positives. DONE_TASK requires explicit `done:` prefix (with colon).

**`scheduler.py` (C14)** — All cron jobs: `coalesce=True`, `max_instances=1`, `misfire_grace_time=5`. The `nudge` job fires every 30 min from `work_start` to `bedtime` on weekdays; `NudgeHandler` decides whether to actually send based on cooldown, free windows, etc.

**`handlers/reminder.py` (C15)** — Multiple concurrent reminders via unique APScheduler job IDs. `parse_reminder()` is a module-level pure function. Reminders stored in `daily_state["reminders"]` and surfaced in LLM context. `misfire_grace_time=300` (5 min, vs 5s for cron jobs) — reminders are more important to deliver.

**`handlers/nudge.py` (C14-N)** — Before sending, checks: `off_today`, weekend, nudge cooldown, recent bot messages, active commitment timers, and (in WORK mode only) whether `now` falls within a calendar free window. In GENERAL/RECOVERY modes, free window check is skipped. Records `last_nudge_ts` in daily state.

**`handlers/followup.py` (C13)** — Single follow-up timer (one at a time, `replace_existing=True`). `TimerPickerView` is public — used by both STUCK handler and externally.

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
- **Joplin write-back is limited to the inbox note.** `create_task()` appends to `todo_inbox_note`; `mark_done()` patches checklist items. No note creation, no folder writes.
- **Google Calendar only.** External calendars imported via ICS subscription URLs in Google Calendar — no separate sync service.
- **User's name is `Gabriell` (two l's)** — stored in `config.json` as `user_name`.
- **Monthly Anthropic API spend cap: `$10` default**, configurable as `monthly_cost_limit_usd`.
- **`secrets/` is gitignored.** Contains `google_token.json`, `google_client_secret.json`, and `pre_implementation_checklist.md`. `config.json` is committed (no secrets).
- **Server-wide security alerts.** The bot listens on every channel it can read in the EVAS server (that's how Discord bots work) and alerts to `security_alerts_channel_id` when a non-owner posts. Channels listed in `monitor_channels` get a per-channel allowlist and are never routed to the LLM — used for scratch/interop channels (e.g., `#claude_general` with ClaMoT) that should be silent to EVA but still protected from strangers.

---

## Bot Behaviour

**Weekday modes:**
1. **Morning** (07:30) — structured interview, one question at a time; retry nudge fires N minutes later if no response. `is_active()` auto-expires after `work_start` to prevent stale state from hijacking message routing.
2. **Work** (09:15–16:00) — maximum pressure; name cost of delay; no soft exits; no feeling questions; no rest suggestions
3. **General** (16:00–20:30) — still consequence-driven; pressure does NOT decrease; tasks still need doing
4. **Recovery** (20:30+) — targets couch/TV idleness specifically; break the distraction, pull attention back to tasks; couch-compatible tasks ([couch]/[low-energy]/[easy]) but framed as "these are easy, you have no excuse"

**Weekends:** silent unless user initiates. Evening nudge configurable via `weekend_evening_nudge`.

**`"off today"`** suppresses all proactive messages for the day (bedtime reminder still fires unless `"full silence"`).

**Nudge cooldown:** 45 min minimum between unsolicited messages. Calendar gap must be ≥ 30 min to trigger a nudge. Periodic nudge fires every 30 min during work hours; also during GENERAL/RECOVERY without free-window gating.

**Tone (first-class feature — this is the single most important design element):**

The language style is **high-pressure, consequence-driven activation language**. It is NOT positive motivation, NOT feel-good, NOT encouraging. It is designed to trigger immediate action by:
- **Pressure**: "Every minute you delay makes it worse."
- **Accountability**: "No one else will do this for you."
- **Loss framing**: "You are actively losing time right now."
- **Future consequences**: "Delay now creates bigger problems later."
- **Identity challenge**: "You're either acting or avoiding—choose."

Short, sharp sentences. No softness, no ambiguity, no comfort. The goal is to make inaction feel unacceptable. The user already knows what to do — the language breaks inertia.

Recovery/evening mode specifically targets TV/couch distraction: "You're not relaxing, you're falling behind." Pull attention back to waiting tasks.

All modes use this style. There is no mode where EVA is "gentle" or "understanding". The old "never guilt/shame" constraint has been deliberately removed. See `notes/observations.md` for 80+ example phrases in the exact target style.

---

## Testing

Tests use `pytest-asyncio` with `asyncio_mode = auto` (in `pytest.ini`). Async test functions work without `@pytest.mark.asyncio`.

Connectors are always mocked in unit tests — no live API calls. Pure functions (`compute_free_windows`, `determine_mode`, `determine_energy`, `detect_intent`, `parse_reminder`) are tested exhaustively without mocks.

Test pattern: `_make_daily(**overrides)` fixture builds a valid `DailyState` dict with sensible defaults. `_make_context()` builds an `AssembledContext`. Both are local to each test file. `conftest.py` in `tests/` is minimal.

---

## Deploy

`./deploy.sh` → SSH to mbox → explicit `docker compose stop bot` (prevents overlapping containers — both would connect to Discord and fire scheduled jobs) → `git pull` → `up -d --build`. The prod override sets `restart: always` and removes host port binding for Joplin. Each deploy appends a timestamped entry to `deploy.log` on mbox.
