# Development Plan

Generated during project kickoff on 2026-03-23. This document is the agreed build contract.

---

## System Description

A self-hosted Discord bot that acts as an executive function prosthetic: proactively nudging, triaging, and decomposing tasks for a single user throughout the day, using Claude as its language backend.

---

## Technology Stack

| Layer | Choice |
|-------|--------|
| Language | Python 3.12 (matches mbox: Ubuntu 24.10) |
| Discord | `discord.py` 2.x |
| Scheduler | `APScheduler` 3.x — `AsyncIOScheduler` (in-process) |
| LLM | Anthropic Python SDK (Sonnet default, Opus on demand) |
| Calendar | `google-api-python-client` + `google-auth-oauthlib` (Google Calendar API v3) |
| Joplin | `aiohttp` → Joplin CLI REST API at `http://joplin:41184` (Docker service name) |
| State | JSON files + `aiofiles` (atomic writes) |
| Config | `config.json` + `.env` for secrets |
| Deployment | Docker Compose — same `docker-compose.yml` for dev (MacBook) and prod (mbox) |

**Docker Compose services:**

| Service | Image | Role |
|---------|-------|------|
| `bot` | Built from `./Dockerfile` | The Discord bot |
| `joplin` | Joplin CLI (community image) | REST API at port 41184 |

**Deployment flow:** `git push` on MacBook → SSH to mbox → `git pull && docker compose up -d --build` (or `./deploy.sh`).

**Google OAuth2 setup:** Run `setup_calendar.py` on MacBook (has browser), copy resulting `secrets/google_token.json` to mbox before first deploy.

**Calendar consolidation:** Google Calendar with ICS subscription URLs from each external provider (Outlook, WorkMail). No separate sync service. See pre-implementation checklist for setup steps. *(Keeper.sh was evaluated and ruled out — it strips event names.)*

---

## Configuration Parameters (relevant additions)

| Parameter | Default | Notes |
|-----------|---------|-------|
| `user_name` | `"Gabriell"` | Bot addresses user by this name |
| `monthly_cost_limit_usd` | `10` | Hard cap on Anthropic API spend; bot warns at 80% |
| `followup_default_min` | `20` | Minutes before follow-up fires after accepted suggestion |
| `joplin_host` | `"joplin"` | Override to `localhost` or an IP when running outside Docker |

## Debug / Time Simulation Mode

The bot has a `--debug` flag that:
- Accepts a simulated start datetime (`--debug-time "2026-03-24 07:25"`)
- Compresses the schedule: 1 real minute = 1 simulated hour (configurable multiplier)
- Prints all state transitions and LLM payloads to stdout
- Disables @mentions (so test runs don't ping the real user)

All time-dependent logic reads from a central `Clock` abstraction (injected dependency) rather than calling `datetime.now()` directly. In production, `Clock` returns real time. In debug mode, it returns simulated time advanced by the multiplier.

## Components

Each component has a single, testable responsibility. Dependencies flow in one direction only.

### C1 — Config Loader
Loads `.env` and `config.json`, validates all required fields, and exposes a single typed `Config` object.
- **Inputs:** `config.json`, `.env`
- **Output:** `Config` dataclass
- **Dependencies:** none

### C2 — State Manager
Async read/write for `state.json`, `interactions.json`, `memory.json`. Atomic writes (write to `.tmp` → rename). Typed accessors for get/set daily state, append interaction log, first-run detection.
- **Inputs:** file paths from Config; structured dicts to persist
- **Output:** current state dicts; persisted files
- **Dependencies:** C1

### C3 — Joplin Connector
Polls Joplin REST API, returns a structured task list with metadata: notebook name, checked status, inline tags, checklist position.
- **Inputs:** `joplin_api_port` from Config
- **Output:** `list[Task]`
- **Dependencies:** C1

### C4 — Calendar Connector
Fetches today's Google Calendar events via API v3; computes free/busy windows for the day. Returns provider-agnostic types (`CalendarEvent`, `FreeWindow`) so that future direct ICS-feed sources can be added without touching anything above this layer.
- **Inputs:** `secrets/google_token.json`, target date
- **Output:** `list[CalendarEvent]`, `list[FreeWindow]`
- **Dependencies:** C1
- **Future extension point:** additional source adapters (direct ICS feeds per provider) feed the same output types; C5 and above are unaffected

### C5 — Context Assembler
Assembles the LLM context payload for a given trigger: determines current mode (Work/Recovery/Weekend), applies time-based energy heuristic, merges tasks + calendar + recent interaction history + state.
- **Inputs:** `list[Task]`, `list[CalendarEvent]`, current state, current datetime
- **Output:** context string, mode enum
- **Dependencies:** C2, C3, C4

Energy heuristic (time-based default, overridden by user declaration):
| Window | Default energy |
|--------|---------------|
| Morning (before `work_start`) | medium |
| Work Mode (`work_start` → `work_end`) | medium |
| Post-lunch (`midday_checkin` ± 1h) | medium-low |
| Recovery Mode (`evening_start` onward) | low |
| Weekend (all day) | low |

First-run guard: if no prior day entry in `state.json`, assembler suppresses all language implying prior history ("you mentioned yesterday", "as we discussed", etc.).

### C6 — LLM Client
Wraps Anthropic SDK. Selects system prompt template by mode, injects context payload, manages Opus session state (`<USE_OPUS>` tag, message counter, auto-revert after `opus_session_max_messages`).
- **Inputs:** mode, context string, user message or trigger description, Opus session state
- **Output:** response string
- **Dependencies:** C1, C2

### C7 — Discord Bot + Router
Entry point. Initializes discord.py client, registers channel and DM listeners. Routes incoming messages and button interactions to the correct handler. Both channel and DM messages are routed to the same handler function — behavior is identical regardless of source.
- **Inputs:** Discord events (messages, button clicks)
- **Output:** dispatched handler calls
- **Dependencies:** C1, all handlers

### C8 — Morning Routine Handler
Stateful multi-turn morning interview. Tracks which questions have been asked in `state.json` (survives bot restart). Asks standard questions one at a time; up to 5 follow-up questions (max 1–3 words answerable). Concludes when standard set is complete or user says "off today".
- **Inputs:** scheduled trigger or retry trigger; user responses
- **Output:** Discord messages; state updated (morning_complete, declared energy, flagged tasks)
- **Dependencies:** C2, C6, C7

### C9 — Day Kick-off Handler
Generates and sends structured day briefing: calendar summary, free windows, top 3 suggested tasks. Uses prior day's baseline if morning routine was skipped. Omits "yesterday" language on first run.
- **Inputs:** scheduled trigger, current state, tasks, calendar
- **Output:** Discord embed with day summary
- **Dependencies:** C2, C5, C6, C7

### C10 — Check-in Handler
Handles midday check-in (brief morning review + afternoon outlook) and evening check-in (Recovery Mode nudge with 1–2 couch-compatible suggestions). Parameterised by check-in type. Buttons + text fallback both update state identically.
- **Inputs:** scheduled trigger, current state, context payload
- **Output:** Discord message with button View; updated state on response
- **Dependencies:** C2, C5, C6, C7

### C11 — Bedtime + End-of-Day Handler
Sends bedtime reminder (personalized rest framing based on day) and optional end-of-day micro-review (generated from `interactions.json`). Two distinct messages at configurable times.
- **Inputs:** scheduled trigger, interaction log, today's state
- **Output:** Discord messages
- **Dependencies:** C2, C6, C7

### C12 — On-Demand Handler
Routes and responds to user-initiated messages:
- `"off today"` → set flag, suppress all proactive messages for remainder of calendar day
- `"I finished X"` → acknowledge, log, optionally suggest next
- `"I'm stuck"` → ask what they're working on, suggest micro-step
- `"Add: ..."` → append to local task queue in `state.json`
- `"<USE_OPUS>"` → activate Opus session
- General free text → pass to LLM in current mode
- **Inputs:** user message text
- **Output:** Discord response; state updates
- **Dependencies:** C2, C5, C6, C7

### C13 — Follow-up Handler
After a suggestion is accepted, schedules a follow-up message at `now + followup_default_min` (default: 20 min). On fire: sends fresh message with new buttons (Done / Still working / Skipped). Cancels the scheduled job if the user reports completion before the timer fires.
- **Inputs:** suggestion acceptance event; follow-up timer fire
- **Output:** APScheduler job created/cancelled; Discord follow-up message with buttons
- **Dependencies:** C2, C7

### C14 — Scheduler
Registers all timed jobs with `AsyncIOScheduler`. All jobs: `coalesce=True`, `max_instances=1`. Weekend suppression: work-mode jobs not registered on Saturday/Sunday.

Jobs registered:
| Job | Time |
|-----|------|
| Morning routine | `morning_routine` |
| Morning retry | `morning_routine` + `morning_routine_retry_window_min` |
| Day kick-off | `work_start` |
| Midday check-in | `midday_checkin` |
| Evening check-in | `evening_start` |
| End-of-day review | `end_of_day_review` |
| Bedtime reminder | `bedtime` |
| Joplin poll | Every 15 min (active hours) / 60 min (overnight) |
| Calendar poll | Every 10 min (active hours) |

Active hours = `morning_routine` time through `bedtime` time. Overnight = everything outside that window.

- **Dependencies:** C1, C3, C4, all handlers

### C15 — Setup Script
One-time `setup_calendar.py`: runs OAuth2 browser consent flow on MacBook, writes `secrets/google_token.json`. Copy token to mbox before first deploy.
- **Inputs:** `secrets/google_client_secret.json`
- **Output:** `secrets/google_token.json`
- **Dependencies:** C1

### C16 — Clock Abstraction
A `Clock` class with a single `now() -> datetime` method. In production, wraps `datetime.now(tz)`. In debug mode, returns a simulated time that advances at a configurable multiplier. Injected into all time-dependent components (Scheduler, Context Assembler, all handlers). Nothing in the codebase calls `datetime.now()` directly.
- **Inputs:** `--debug` flag, simulated start time, time multiplier
- **Output:** current datetime (real or simulated)
- **Dependencies:** C1
- **Testability:** Inject a fixed or advancing mock clock; all time logic becomes deterministic

### C17 — Cost Tracker
Tracks cumulative Anthropic API spend for the current calendar month (estimated from token counts returned in API responses). Persists monthly totals to `state.json`. Refuses LLM calls and sends a Discord warning when the monthly cap is reached. Sends a warning message at 80% of the cap.
- **Inputs:** token usage from each LLM API response; `monthly_cost_limit_usd` from Config
- **Output:** updated spend record in state; Discord warning messages at threshold and cap
- **Dependencies:** C1, C2, C7
- **Testability:** Inject mock token counts; assert warning fires at 80%; assert LLM calls blocked at 100%

### C18 — Docker Stack + Deploy Script
`Dockerfile` for the bot, `docker-compose.yml` defining `bot` and `joplin` services, and `deploy.sh` (SSH to mbox, git pull, docker compose up). The same compose file is used for dev and prod; only `.env` differs.
- **Inputs:** source code, `.env`, `config.json`
- **Output:** running bot + Joplin containers
- **Dependencies:** all components
- **Testability:** `docker compose up` on MacBook reaches a healthy bot and Joplin API

---

## Development Phases

Each phase produces something that runs and can be verified.

### Phase 1-A: Skeleton — stack runs, bot connects, state initializes

**Components:** C1, C2, C7 (message echo only), C16, C18

`docker compose up` on MacBook brings up both `joplin` and `bot` containers. Bot connects to Discord, reads config, initializes `data/` with correct empty JSON files on first run, echoes any message back. Channel and DM routing both work through the same handler. `--debug` flag produces simulated time output.

**Acceptance criterion:** `docker compose up` on MacBook: both containers healthy. Bot appears online in Discord. Message in configured channel returns an echo addressed to "Gabriell". DM returns the same. `data/state.json`, `data/interactions.json`, `data/memory.json` created with correct empty schemas. Running with `--debug --debug-time "2026-03-24 07:25"` produces simulated time in logs.

---

### Phase 1-B: Connectors verified independently

**Components:** C3, C4, C15

Joplin connector talks to the `joplin` container (already running from Phase 1-A). Calendar connector verified after running `setup_calendar.py` on MacBook. Both runnable as standalone scripts inside the bot container.

**Acceptance criterion:** `docker compose exec bot python -m connectors.joplin` prints a structured task list from the Joplin container. `python setup_calendar.py` on MacBook completes OAuth2 and writes `secrets/google_token.json`. `docker compose exec bot python -m connectors.calendar` prints today's events and free windows.

---

### Phase 1-C: Context assembly and LLM response

**Components:** C5, C6

Context assembler and LLM client runnable end-to-end as a standalone script before Discord wiring.

**Acceptance criterion:** `python -m context.assembler` prints assembled context for the current moment — correct mode, energy level, task list, calendar. First-run check: no "yesterday" language when `state.json` has no prior day entry. `python -m llm.client` sends the assembled context to Claude and prints a coherent response.

---

### Phase 1-D: Scheduled check-ins wired end-to-end

**Components:** C8, C9, C10, C11, C14 (morning, kick-off, evening, bedtime, end-of-day jobs)

All five scheduled message types fire at configured times and send correctly structured Discord messages with button Views. Text fallback accepted for all buttons. Morning retry fires if no response within `morning_routine_retry_window_min`.

**Acceptance criterion:** Temporarily advance schedule times and verify: morning routine sends correct questions in sequence; morning retry fires with shorter message if unanswered; day kick-off sends embed with calendar + top tasks; midday check-in sends with correct buttons; evening check-in sends couch-compatible suggestions; bedtime reminder fires. Button clicks and typed equivalents ("done", "skip") produce identical state updates.

---

### Phase 1-E: On-demand + weekend mode + "off today" + follow-up

**Components:** C12, C13, C14 (complete)

All on-demand intents, weekend suppression, "off today" flag, follow-up scheduling, and interaction logging complete. End-of-day summary generated from `interactions.json`.

**Acceptance criterion:** All spec Section 10 scenarios produce correct behavior:
- "off today" acknowledged immediately; all subsequent proactive messages suppressed; bedtime reminder still fires
- Saturday run: no morning interview, no day kick-off; evening nudge fires
- Follow-up fires 20 min after suggestion accepted; early "done" cancels it
- "I'm stuck", "I finished X", "Add: ...", general chat all produce correct responses
- End-of-day summary generated from interaction log entries and reflects the day's exchanges

---

## Conventions

1. **File structure:** `handlers/`, `connectors/`, `context/`, `llm/`, `state/` are Python packages (with `__init__.py`). Entry point is `bot.py`. Each module is importable and runnable independently where practical.

2. **Naming:** `snake_case` throughout. Config keys match parameter names in the spec exactly (e.g., `morning_routine`, `followup_default_min`). State dict keys are also `snake_case`.

3. **Testing:** Each component is tested via `pytest`. Tests live in `tests/` mirroring the source structure (e.g., `tests/connectors/test_joplin.py`). External APIs (Discord, Joplin, Google, Anthropic) are always mocked in tests. Tests run before any component is considered complete.

4. **Error handling:** All external calls (Joplin, Calendar, Claude API) have explicit try/except with logging. On connector failure, the bot degrades gracefully: missing Joplin data → skip task context; missing Calendar data → skip calendar context; LLM failure → send a safe fallback message rather than silently failing.

5. **Secrets and gitignore:** `.env`, `secrets/google_token.json`, `secrets/google_client_secret.json`, and `data/` are gitignored before the first commit. `config.example.json` and `.env.example` are committed as templates.

6. **Commits:** One commit per completed component or meaningful checkpoint. Never commit a component whose tests are failing.
