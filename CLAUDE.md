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
| Joplin | `aiohttp` → `http://joplin:41184` | Joplin CLI in Docker; read + limited write (inbox append, mark done) |
| Calendar | Google Calendar API v3 | OAuth2, `calendar.readonly` + `calendar.events` (write) |
| State | JSON files + `aiofiles` | **Not SQLite.** `state.json`, `interactions.json`, `memory.json` |
| Deployment | Docker Compose | Same `docker-compose.yml` for dev (MacBook) and prod (mbox) |
| Timezone | `Europe/Berlin` | All scheduling and time logic |

**Services:** `eva-bot-dev` (bot container), `eva-joplin-dev` (Joplin CLI + socat forwarder).

**Joplin network note:** Joplin's REST API only binds to `127.0.0.1` inside its container. The entrypoint runs Joplin on internal port 41185 and uses `socat` to forward `0.0.0.0:41184 → 127.0.0.1:41185` so the bot can reach it via Docker DNS (`http://joplin:41184`).

**Prod deploy:** `./deploy.sh` → SSH to mbox (`~/services/exec_func_assist`) → explicit `docker compose stop bot` → `git pull` → `up -d --build`. The explicit stop before rebuild prevents overlapping containers (both would connect to Discord and fire scheduled jobs simultaneously). Each deploy appends a timestamped entry to `deploy.log` on mbox. The prod override sets `restart: always` and removes host port binding for Joplin.

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

### Key Modules

**`config.py` (C1)** — Frozen `Config` dataclass. Loads secrets from `.env`, settings from `config.json`. Raises `ConfigError` on missing values. `config.json` is committed (no secrets); `.env` is gitignored. Notable optional field: `security_alerts_channel_id` (Discord channel for unauthorized-message alerts; `null` = log-only).

**`state/manager.py` (C2)** — `StateManager`: async read/write for three JSON files. Writes are atomic (`.tmp` → rename). Daily rollover (archives `daily` → `previous_daily`) happens automatically on every `get_daily()` / `update_daily()` call when the clock date changes — no restart needed. Key methods: `get_daily()`, `update_daily(**kwargs)`, `append_interaction()`, `get_recent_interactions(n)`, `has_previous_daily()`.

**`connectors/models.py`** — Shared output types: `Task`, `CalendarEvent`, `FreeWindow`. These are the contract between connectors and `ContextAssembler`. New calendar sources only need to produce these types.

**`connectors/joplin.py` (C3)** — Reads and writes Joplin via REST API. Two read sources: standalone todo notes (`is_todo=1`) and unchecked checklist items in regular note bodies. Tags extracted via `_TAG_RULES`. Write operations: `create_task(title)` appends a checklist item to the configured inbox note (`todo_inbox_note`, default `"99 - added by eva"`); `mark_done(task)` patches the checklist item from `- [ ]` to `- [x]`. Returns `[]` / gracefully degrades on failure.

**`connectors/calendar.py` (C4)** — Enumerates all selected calendars via `calendarList.list` (not just `primary`). Fetches events per calendar. Pure function `compute_free_windows()` computes free time slots. `create_event(title, start, end, calendar_id)` creates a Google Calendar event. Excluded calendars configured via `excluded_calendar_ids` in `config.json`. `last_fetch_failed` flag is set after each `get_events()` call — `True` on connector failure, `False` on success. Passed through to the context assembler so the LLM context warns "CALENDAR UNAVAILABLE" instead of silently showing empty.

**`context/assembler.py` (C5)** — Pure functions `determine_mode()` and `determine_energy()` are module-level (testable without class). `ContextAssembler.assemble()` takes pre-fetched data and returns `AssembledContext` with a formatted `text` field ready for the LLM. Timed calendar events are labelled `[past]`, `[now]`, or `[upcoming]` relative to `clock.now()` so the LLM cannot confuse an event's start time with the current time.

**`llm/client.py` (C6)** — `LLMClient.send()` selects Sonnet/Opus based on session state, tracks monthly spend in `state.json`, enforces `monthly_cost_limit_usd`. Opus auto-reverts after `opus_session_max_messages`. **Single-turn model:** each call sends one `user` message with the full context string (tasks, calendar, factual exchange log) in the system prompt. No multi-turn API history — this is deliberate to prevent tone contamination across mode transitions.

**`llm/prompts.py`** — System prompts keyed by `Mode` enum. Tone is a first-class feature. **Hardcoded trigger strings inside each handler (`fire()`, `fire_end_of_day()`, etc.) are equally load-bearing** — they are the user-turn instruction that shapes the LLM output. If you change tone, update both `prompts.py` AND the trigger strings in the relevant handler. The end-of-day trigger includes `clock.now()` date+time explicitly to prevent day-of-week hallucination.

**`utils/clock.py` (C16)** — `Clock` abstraction. `RealClock` for production; `DebugClock` for time-simulation (configurable multiplier). **Nothing calls `datetime.now()` directly** — always use `clock.now()`.

**`bot.py` (C7)** — `EFABot(discord.Client)`. Both channel and DM messages enter `_handle_message()`. Morning routine takes priority when active; all other messages route through `OnDemandHandler`. `_build_bot()` factory wires all handlers; `on_ready()` injects APScheduler into `FollowupHandler` after the scheduler starts (avoids circular dependency). `on_message` enforces `discord_user_id` — all other authors are silently dropped and optionally reported to `security_alerts_channel_id` via `_alert_unauthorized()`.

**`handlers/base.py`** — `BaseHandler` superclass. Provides `_log_bot(msg)` and `_log_user(msg)` for interaction logging, plus the `SendFn` type alias. All handlers extend this.

**`handlers/morning.py` (C8)** — Stateful multi-turn morning interview. `fire()` / `fire_retry()` for scheduled triggers; `handle_response()` for user replies; `is_active()` to check routing priority.

**`handlers/kickoff.py` (C9)** — Sends the LLM-generated day briefing at `work_start`.

**`handlers/checkin.py` (C10)** — Parameterised by `CheckinType` (MIDDAY / EVENING). `fire(type, send_fn)` sends LLM message + `_CheckinView` buttons. `handle_text_response()` accepts typed equivalents.

**`handlers/bedtime.py` (C11)** — `fire_end_of_day()` generates an LLM micro-review from `interactions.json` (skipped if `off_today`). `fire_bedtime()` sends a fixed message (only skipped if `off_today_full_silence`).

**`handlers/on_demand.py` (C12)** — Module-level `detect_intent(text) -> Intent` pure function (testable without class). Intents: `OFF_TODAY`, `FINISHED`, `DONE_TASK`, `STUCK`, `SKIP`, `ADD_TASK`, `ADD_EVENT`, `REMINDER`, `COMMIT`, `USE_OPUS`, `TRIGGER`, `GENERAL`. Key behaviours: FINISHED and STUCK use `re.match` (start-of-message only) to avoid false positives on natural sentences; DONE_TASK requires explicit `done:` prefix (with colon) or Discord buttons — no implicit matching; COMMIT matches `"I need N min"`, `"give me N min"`, `"check back in N min"`, `"remind me in N min"`, `"I need another N min"`, `"timer N min"`, bare `"N min"` — sets a user-defined APScheduler timer; STUCK calls LLM then shows `TimerPickerView` (no auto-schedule); FINISHED cancels any pending timer. `set_scheduler()` called post-construction.

**`scheduler.py` (C14)** — Registers all APScheduler cron jobs. All jobs: `coalesce=True`, `max_instances=1`, `misfire_grace_time=5`. **Every `CronTrigger` must have `timezone=tz` explicitly** — the scheduler's timezone does NOT propagate to triggers; omitting it causes jobs to fire on UTC (1 hour early in Europe/Berlin). Weekend suppression is via `day_of_week` on the trigger. `Scheduler.trigger(name, send_fn=None)` fires any named job manually. The `nudge` job fires every 30 min from `work_start` to `bedtime` on weekdays; the `NudgeHandler` decides whether to actually send based on cooldown, free windows, etc.

**`handlers/followup.py` (C13)** — `FollowupHandler` schedules a one-shot APScheduler `date` job. `schedule(suggestion, minutes=None)` — `minutes` overrides `config.followup_delay_min` (default 20); stores `commitment_minutes` in state so `_fire()` shows the actual committed time. `handle_timer_set(suggestion, minutes, send_fn)` — called by `TimerPickerView` buttons. `TimerPickerView` (public) — Discord UI offering [10 / 20 / 30 / 45 min / No timer] buttons, attached to STUCK responses. `cancel()` silently tolerates missing job or absent scheduler.

**`handlers/reminder.py` (C15)** — `ReminderHandler` manages user-requested timed reminders. Each reminder becomes a unique APScheduler `date` job (multiple concurrent reminders supported). `schedule(text, run_at)` creates a job; `cancel(job_id)` / `cancel_all()` remove them. Active reminders are stored in `daily_state["reminders"]` and surfaced in the LLM context string. `parse_reminder(text, now, tz)` is a module-level pure function that extracts time and text from patterns like "remind me at 14:30 about X", "remind me tomorrow at 09:45: X", "remind me on friday at 13:00: X". Intent detection in `on_demand.py` dispatches REMINDER intent before COMMIT to avoid "remind me at HH:MM" being swallowed by "remind me in N min".

**`handlers/nudge.py` (C14-N)** — `NudgeHandler` fires every 30 minutes during work hours via a cron job in `scheduler.py`. Before sending, it checks: `off_today`, weekend, nudge cooldown (`nudge_cooldown_min`), recent bot messages, active commitment timers, and (in WORK mode) whether `now` falls within a calendar free window. In GENERAL and RECOVERY modes, free window check is skipped. Records `last_nudge_ts` in daily state.

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

---

## Bot Behaviour

**Weekday modes:**
1. **Morning** (07:30) — structured interview, one question at a time; retry nudge fires N minutes later if no response. `is_active()` auto-expires after `work_start` to prevent stale state from hijacking message routing.
2. **Work** (09:15–16:00) — maximum pressure; name cost of delay; no soft exits; no feeling questions; no rest suggestions
3. **General** (16:00–20:30) — still consequence-driven; pressure does NOT decrease; tasks still need doing
4. **Recovery** (20:30+) — targets couch/TV idleness specifically; break the distraction, pull attention back to tasks; couch-compatible tasks ([couch]/[low-energy]/[easy]) but framed as "these are easy, you have no excuse"

**Weekends:** silent unless user initiates. Evening nudge configurable via `weekend_evening_nudge`.

**`"off today"`** suppresses all proactive messages for the day (bedtime reminder still fires unless `"full silence"`).

**Nudge cooldown:** 45 min minimum between unsolicited messages. Calendar gap must be ≥ 30 min to trigger a nudge.

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

Tests use `pytest-asyncio`. Async test functions work without `@pytest.mark.asyncio` — check `pytest.ini` or `pyproject.toml` for `asyncio_mode = auto`.

Connectors are always mocked in unit tests — no live API calls. `compute_free_windows()` and `determine_mode()`/`determine_energy()` are pure functions tested exhaustively without mocks.

The `conftest.py` in `tests/` sets up shared fixtures.
