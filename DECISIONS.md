# Architectural Decisions

All significant decisions for `exec_func_assist`. Format: decision, options considered, rationale, date.

---

## D1 — In-process scheduler (APScheduler AsyncIOScheduler)

**Decision:** Use APScheduler's `AsyncIOScheduler` running inside the discord.py asyncio event loop.

**Options considered:**
- A: APScheduler in-process (chosen)
- B: Separate cron jobs invoking the bot via an internal API
- C: A second thread running a synchronous scheduler

**Rationale:** Option A shares the event loop with discord.py — scheduled jobs can call Discord send methods directly without IPC or thread-safety concerns. Single process keeps deployment simple. Options B and C add unnecessary complexity for a single-user, self-hosted bot.

**Date:** 2026-03-23

---

## D2 — aiohttp for all outbound HTTP

**Decision:** Use `aiohttp` (async) for all HTTP calls, including Joplin REST API and any future integrations.

**Options considered:**
- A: `requests` (synchronous)
- B: `aiohttp` (async, chosen)
- C: `httpx` (async alternative)

**Rationale:** discord.py runs on asyncio. Synchronous `requests` calls would block the event loop and stall the entire bot during Joplin polls. `aiohttp` is the standard async HTTP client for the Python asyncio ecosystem. `httpx` is a valid alternative but `aiohttp` is more widely used with discord.py.

**Date:** 2026-03-23

---

## D3 — Polling for Joplin and Google Calendar (no webhooks)

**Decision:** Poll Joplin every 15 min during active hours / 60 min overnight. Poll Google Calendar every 10 min during active hours.

**Options considered:**
- A: Polling (chosen)
- B: Joplin webhooks (not available — Joplin REST API has no push support)
- C: Google Calendar push notifications (requires a public HTTPS endpoint — impractical on a home server without a static IP or tunnel)

**Rationale:** Neither external system makes webhooks practical in this hosting environment. Polling latency (≤15 min) is acceptable for a personal productivity assistant operating on a human time scale.

**Date:** 2026-03-23

---

## D4 — Rolling LLM context window, not full conversation history

**Decision:** Each LLM call receives the last 3–5 exchanges plus the freshly assembled context payload. Full conversation history is not passed.

**Options considered:**
- A: Pass full conversation history on every call
- B: Rolling window of recent exchanges + structured context (chosen)

**Rationale:** Full history grows unbounded and would eventually exceed context limits or become expensive. The bot's "memory" is the structured state store — not LLM history. A rolling window keeps token costs predictable and forces relevant context to be explicit rather than buried in a long transcript.

**Date:** 2026-03-23

---

## D5 — Fresh Discord message for every follow-up (no button persistence)

**Decision:** Every follow-up or re-prompt sends a new Discord message with a new button View. Old messages are left as-is (buttons silently become inert after Discord's 15-minute server-side limit).

**Options considered:**
- A: `View(timeout=None)` with bot-restart re-registration to persist buttons
- B: Always send a fresh message with new buttons (chosen)

**Rationale:** Discord invalidates button components 15 minutes after they are sent, regardless of View timeout settings on the bot side. Option A requires storing View state and re-registering on every restart — significant complexity for no user-visible benefit. Option B is simpler and the UX is equivalent: the user always sees a fresh, actionable message.

**Date:** 2026-03-23

---

## D6 — State split across domain-scoped JSON files

**Decision:** Three JSON files: `state.json` (daily/session state), `interactions.json` (interaction log), `memory.json` (Phase 2 memory, empty stub in Phase 1). Atomic writes via temp-file-then-rename.

**Options considered:**
- A: Single `state.json` for everything
- B: SQLite database
- C: Domain-scoped JSON files (chosen)

**Rationale:** SQLite was explicitly ruled out in the spec (simplicity and portability). A single flat JSON file risks schema bloat and makes it harder to reason about which part of the system owns which data. Domain-scoped files give clear ownership boundaries and make Phase 2 memory implementation non-breaking (the file and schema already exist). Atomic writes prevent corruption on process kill.

**Date:** 2026-03-23

---

## D7 — Channel + DM parity via single handler

**Decision:** The bot listens to a single configured channel and to DMs. Both are routed through the same handler function — behavior is identical regardless of message source.

**Options considered:**
- A: Channel only
- B: Channel + DM with separate handlers
- C: Channel + DM with single shared handler (chosen)

**Rationale:** The user wants to be able to interact with the bot from mobile (DM) and desktop (channel) interchangeably. Duplicating handler logic introduces drift. `isinstance(message.channel, discord.DMChannel)` is sufficient to detect source when needed (e.g., for sending proactive messages to the right destination).

**Date:** 2026-03-23

---

## D8 — Fixed follow-up window, not LLM-parsed duration

**Decision:** Follow-up fires at `now + followup_default_min` (default: 20 minutes, configurable). Duration is not parsed from the LLM-generated message body.

**Options considered:**
- A: Parse estimated duration from the LLM response (e.g., "~15 min" → schedule 15 min follow-up)
- B: Fixed configurable window (chosen)

**Rationale:** Parsing structured data from a natural-language LLM response is fragile — format varies, extraction can fail silently, and errors produce confusing follow-up timing. If variable timing is needed later, the LLM client will set an explicit structured field on the suggestion object (separate from the message body), not parse the prose.

**Date:** 2026-03-23

---

## D9 — LLM-inferred task priority, no algorithmic scoring

**Decision:** Task priority is inferred by the LLM from the assembled context (notebook name, checklist position, inline tags, note content). No point-scoring algorithm.

**Options considered:**
- A: Algorithmic priority score (tag weights, position index, deadline proximity)
- B: LLM-inferred from holistic context (chosen)

**Rationale:** The user's primary control lever is inline tags (`[high]`, `[couch]`, `[low-energy]`). Beyond that, contextual judgment (energy level, time of day, what was recently worked on) is exactly what LLMs do well. A rigid algorithm would need to be tuned and maintained as usage patterns evolve. The LLM can weigh competing signals holistically without explicit rules.

**Date:** 2026-03-23

---

## D10 — Config file only in Phase 1; Discord config commands deferred to Phase 3

**Decision:** User edits `config.json` directly in Phase 1. Discord-based config commands (`!config set ...`) are a Phase 3 feature.

**Options considered:**
- A: Discord config commands from Phase 1
- B: Config file only in Phase 1, Discord commands in Phase 3 (chosen)

**Rationale:** Discord config commands require parsing, validation, confirmation flows, and error messaging — meaningful complexity that delays Phase 1 delivery with low payoff. The user can edit the config file directly during early development. Phase 3 is the appropriate point for quality-of-life features.

**Date:** 2026-03-23

---

## D11 — Google Calendar as sole calendar integration

**Decision:** Integrate only with Google Calendar via API v3 (OAuth2, read-only). All other calendars (Outlook/work, AWS WorkMail, Proton) are consolidated into Google Calendar by the user before implementation begins.

**Options considered:**
- A: Native integration with each calendar provider
- B: Google Calendar as single aggregation point (chosen)

**Rationale:** Multi-provider integration multiplies auth complexity, API surface, and maintenance burden. Google Calendar is well-documented, has a mature Python client library, and the user is willing to consolidate calendars there as a one-time setup step. This is a pre-implementation prerequisite for the user, not the bot.

**Date:** 2026-03-23

---

## D12 — memory.json created as empty stub in Phase 1

**Decision:** `memory.json` is created on first run with the correct empty schema in Phase 1. Nothing reads or writes to it until Phase 2.

**Options considered:**
- A: Don't create memory.json until Phase 2 (requires a migration/creation step)
- B: Create stub with correct schema in Phase 1 (chosen)

**Rationale:** Creating the file early means Phase 2 memory implementation has no schema migration — it just starts populating an already-existing structure. This is a low-cost, low-risk investment that keeps Phase 2 non-breaking.

**Date:** 2026-03-23

---

## D13 — Docker Compose as deployment unit; Joplin containerized

**Decision:** The entire stack (bot + Joplin CLI) runs in Docker Compose. Joplin moves from bare Desktop install to a containerized Joplin CLI instance. The same `docker-compose.yml` is used for both local development (MacBook) and production (mbox).

**Options considered:**
- A: Bot in Docker, Joplin as bare Desktop install on mbox (original plan)
- B: Bot in Docker, Joplin CLI in Docker, same Compose file for dev and prod (chosen)

**Rationale:** Running Joplin in the same Compose network eliminates the `network_mode: host` workaround entirely — the bot reaches Joplin at `http://joplin:41184` via Docker's internal DNS. More importantly, it enables full local development on MacBook: spin up the entire stack with `docker compose up` without touching mbox. Dev and prod environments are structurally identical; only `.env` differs.

**Date:** 2026-03-24

---

## D14 — Bot-to-Joplin connection uses Docker service name

**Decision:** `joplin_host` config defaults to `joplin` (the Docker Compose service name). In the Compose network the bot reaches Joplin at `http://joplin:41184`. No host networking needed.

**Options considered:**
- A: `network_mode: host` so `localhost:41184` works inside the container
- B: Docker internal DNS via service name (chosen)

**Rationale:** Service-name addressing is the standard Docker Compose pattern. It avoids polluting the host network namespace and works identically on MacBook and mbox. If ever run outside Docker (e.g., bare Python for quick debugging), `joplin_host` can be overridden to `localhost` or an IP.

**Date:** 2026-03-24

---

## D15 — Calendar consolidation: ICS subscription URLs into Google Calendar

**Decision:** Use Google Calendar's native "Add calendar from URL" feature with ICS subscription links from each calendar provider (Outlook, AWS WorkMail). The bot integrates with Google Calendar only via the API v3. No separate sync service.

**Options considered:**
- A: ICS subscription URLs in Google Calendar — **chosen**
- B: Bot integrates directly with multiple ICS feeds (more connectors, no Google dependency) — deferred, keep door open
- C: Keeper.sh — **ruled out**: only syncs timeslots, does not preserve event summaries or descriptions

**Rationale:** ICS subscription is a one-time user-side setup with no ongoing service. Keeper.sh was rejected because it strips event names. Option B remains architecturally viable and is kept open for a future phase — the Calendar Connector (C4) is designed to return a provider-agnostic `list[CalendarEvent]`, so adding direct ICS sources later only requires a new data source feeding the same output type; the Context Assembler and everything above it are unaffected.

**Date:** 2026-03-24
