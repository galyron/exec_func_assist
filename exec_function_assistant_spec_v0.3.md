# Executive Function Assistant — Client Specification

**Discord Bot · Cross-Device Productivity System**

**Version 0.3 · March 2026**

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Solution Overview](#2-solution-overview)
3. [Interaction Modes](#3-interaction-modes)
4. [Weekend Behavior](#4-weekend-behavior)
5. [Skipped Routines and Off Days](#5-skipped-routines-and-off-days)
6. [Multi-User Considerations](#6-multi-user-considerations)
7. [Context Pipeline](#7-context-pipeline)
8. [Technical Architecture](#8-technical-architecture)
9. [User Configuration](#9-user-configuration)
10. [Example Interaction Flows](#10-example-interaction-flows)
11. [Phased Delivery Plan](#11-phased-delivery-plan)
12. [Open Questions and Decisions](#12-open-questions-and-decisions)
13. [Success Criteria](#13-success-criteria)
14. [Closing Remarks](#14-closing-remarks)

---

## 1. Problem Statement

This specification defines a Discord-based assistant designed to function as an **executive function prosthetic**. The system addresses three interconnected problems experienced by the user.

### 1.1 The Initiation Problem

The user frequently struggles to begin tasks regardless of their nature (chores, admin work, data analysis, development, project work). The root causes are varied and often compound:

- **Ambiguity:** Tasks are defined at too high a level ("do taxes", "refactor the pipeline") and the brain cannot find a concrete grip point to start.
- **Aversion:** The task is boring, unpleasant, or emotionally loaded, generating escape impulses that are hard to override.
- **Overwhelm:** Too many competing tasks make selection itself feel like a task, leading to decision paralysis.
- **Transition friction:** Switching from one cognitive mode to another (or from rest to work) has a high activation cost.
- **Depletion:** After cognitively demanding periods (particularly parenting), executive function resources are genuinely depleted. Willpower-based approaches fail in this state.

### 1.2 The Fragmentation Problem

The user's time is highly fragmented across the day. Context switches and interruptions prevent sustained focus. Available work windows are often short and unpredictable, making it hard to plan and start deep work sessions.

### 1.3 The Awareness Problem

Tasks and commitments are distributed across multiple systems (Joplin notebooks, Google Calendar, emails, mental backlog) with no unified view. The user cannot act effectively on what they cannot see in aggregate.

---

## 2. Solution Overview

The system is a Discord bot backed by an LLM (Claude API) that provides context-aware, energy-sensitive productivity support throughout the day. It operates in three primary modes that blend across daily transitions.

### 2.1 Morning Routine (Expectation Setting)

A short, structured interview to assess mood, rest level, energy, and pressing issues — and to align on which tasks should realistically be accomplished given the day's constraints. The bot initiates this conversation at a configurable time (default: 07:30). The user replies at their own pace during the morning.

The bot asks a fixed set of standardized questions and may ask up to **5 follow-up questions**. Follow-up questions must be answerable in 1–3 words.

**Standardized questions:**
- How was your night?
- How much sleep did you get?
- How energized do you feel?
- Is there a particular task on your mind?
- Is there a particularly urgent thing you must finish today?
- Is there something else pressing you today?
- What's the one thing that would make today feel productive?

### 2.2 Work Mode (Daytime Hours)

Active during configurable working hours. More assertive in tone. Focused on triage, task decomposition, momentum maintenance, and helping the user decide what to work on and how to start.

### 2.3 Recovery Mode (Evening Hours)

Active after children's bedtime. Recognizes depleted executive function. Suggestions are gentler, tasks are matched to low energy, commitments are kept small (15-minute contracts), and the tone is supportive rather than demanding. Tasks tagged as couch-compatible (`[couch]`, `[easy]`, `[low-energy]`) are prioritized.

### 2.4 Design Principles

- **The bot initiates, not the user.** The primary interaction model is proactive nudges from the bot, not user-initiated requests. The user is unlikely to reach out when stuck — the bot must notice opportunities and act.
- **Reduce friction to zero.** Every interaction should require the minimum possible effort from the user. One-tap responses, short messages, no lengthy setup rituals.
- **Energy-aware, not guilt-driven.** The bot never guilts, shames, or pressures. It matches its suggestions to estimated energy levels and always offers an easy exit.
- **Concrete over abstract.** Never suggest "work on Project X." Always suggest the specific next physical action: "Open the repo, run the tests, read the first failing test name."
- **Small bets.** Default commitment is 15 minutes. Permission to stop is always explicit. Getting started matters more than finishing.

---

## 3. Interaction Modes

*(Listed in priority order)*

### 3.1 Proactive Nudges *(Highest Priority)*

The bot monitors the user's calendar and task list and sends contextually appropriate suggestions without being asked.

**Triggers:**
- Morning start-of-day (configurable, default 07:30) — Morning Routine, followed by a Day Kick-off briefing.
- A calendar gap of 30+ minutes is detected with no meeting within 15 minutes before or after.
- A deadline is approaching within 48 hours and the associated task has not been marked as started.
- Post-lunch return (configurable, default 13:00) — a re-engagement nudge.
- Evening window opens (configurable, default 20:30) — a gentle Recovery Mode nudge.
- A previously suggested task was acknowledged but no completion was reported after the estimated duration.

**Example nudge (Work Mode):**
> "You have 90 minutes before your 3 PM call. Your Joplin 'GoTrial' notebook has a task marked high: 'Finalize QA documentation.' Want to start with just reviewing the last section you edited? Should take about 15 minutes."

**Example nudge (Recovery Mode):**
> "Kids down? You've got about 2 hours before a reasonable bedtime. Here's something small you could do from the couch: review and respond to those 3 flagged emails (~15 min). Or if you've got a bit more energy: draft the intro paragraph for the project proposal (~25 min). Both are couch-friendly. No pressure on the deep stuff tonight."

### 3.2 Structured Check-Ins *(High Priority)*

Short, structured prompts at key transition points. These build self-awareness and provide natural planning moments.

**Morning Routine:** Short interview to assess mood, rest, energy, and pressing issues (see Section 2.1).

**Day Kick-off:** Sent at `work_start`.
- Summary of today's calendar (meetings, gaps, total available work time).
- Top 3 suggested tasks based on priority, deadline proximity, and estimated effort.

**Midday Check-In:** Sent at `midday_checkin`.
- Brief review: "This morning you planned to do X. Did that happen?" (yes/no buttons)
- Afternoon outlook: remaining meetings, available time, adjusted suggestions.

**Evening Check-In:** Sent at `evening_start`.
- Transition message acknowledging energy state shift.
- 1–2 low-effort suggestions with explicit couch-compatibility flags.
- Option to skip: "If tonight's a rest night, that's fine. Want me to check in again tomorrow morning?"

**End-of-Day Micro-Review:** Optional. A brief "what got done today" summary generated from the bot's interaction log. Sent at a configurable time (default 22:30). Not to guilt, but to build a pattern of awareness over time. The user has the chance to flag any important task that must be carried forward.

**Bedtime Reminder:** A crafted message that encourages the user to start their bedtime routine at the configured time (default 23:00). The message highlights personalized reasons why rest would be particularly beneficial that evening (e.g., demanding day, cognitively heavy tasks tomorrow, cumulative sleep debt). Health benefit framing is appropriate here.

### 3.3 Task Decomposition *(Medium Priority)*

The user can reference a task (by pasting text or pointing to a Joplin note) and the bot breaks it into a sequence of concrete actions, each scoped to a single work session (15–45 minutes). The bot identifies the very first physical action and highlights it.

**Example:** User says "I need to set up the data pipeline for the new registry." The bot responds with 5–7 concrete steps, the first being something like: "Step 1: Open the existing pipeline repo and read the README to remind yourself of the current architecture. ~10 minutes."

### 3.4 On-Demand Interaction *(Lower Priority)*

The user can message the bot at any time. Supported intents include:

- `"I'm stuck"` — the bot asks what they're working on and suggests the next micro-step.
- `"What should I do?"` — the bot triages the task list given current time, energy estimate, and calendar.
- `"I finished X"` — the bot acknowledges, updates internal state, and optionally suggests the next thing.
- `"Skip"` / `"Not now"` — the bot backs off gracefully, no guilt.
- Free-text task entry: `"Add: call insurance company about claim"` — adds to a local task queue.

### 3.5 Adaptive Responses

The bot is adaptive and reacts to feedback. If the user dismisses a nudge for a specific reason or requests a more fitting task, the bot adjusts its suggestion dynamically — applying whatever criteria the user specifies (energy level, type of work, duration, etc.).

### 3.6 General LLM Conversation

The bot exposes the regular LLM interface for any topic the user wishes to discuss. This mode is intentionally basic (standard chat), but the architecture must remain open to more elaborate operations in future phases (e.g., inserting calendar blockers, drafting invites).

**Model escalation:** The user may request Opus via the tag `<USE_OPUS>`. When active, the bot's responses will include a visible reminder that Opus is in use. Opus mode applies for a configurable number of messages or time window before reverting to Sonnet.

---

## 4. Weekend Behavior

Weekends are treated differently from weekdays. The bot operates in **Recovery Mode all day** on Saturdays and Sundays — there is no Work Mode, no Day Kick-off, and no proactive work nudges.

Weekend-specific behavior:

- **Morning Routine:** Skipped. The bot does not initiate a morning interview on weekends.
- **Daytime:** The bot is silent unless the user initiates a conversation.
- **Evening nudge:** Sent as usual at `evening_start`. Suggestions are limited to household tasks, personal projects, and other non-work items. Couch-compatible tasks are preferred.
- **Bedtime reminder:** Sent as on weekdays.

> This default is expected to evolve. Weekend evening nudge behavior may be adjusted or made configurable based on experience.

---

## 5. Skipped Routines and Off Days

### 5.1 Missed Morning Routine

If the user does not respond to the Morning Routine within a configurable window (default: 90 minutes after the morning message is sent), the bot will **send one follow-up retry** with a shorter, lower-friction version of the check-in (e.g., "Still there? Even a quick energy check — how are you feeling this morning?").

If there is still no response after the retry:
- The Day Kick-off message is sent at `work_start` using **yesterday's state** (last known energy level, previous task priorities) as a fallback.
- The bot notes internally that the morning routine was skipped and takes this into account in its tone for the rest of the day (slightly less assertive, more checking in).

### 5.2 The "Off Today" Command

The user can send `"off today"` (or a natural-language equivalent the bot should recognize) at any point during the day. The bot will:

1. Acknowledge the message briefly and warmly (no pushback, no guilt).
2. Suppress all proactive nudges, check-ins, and suggestions for the **remainder of the calendar day**.
3. Still send the **bedtime reminder** unless the user also says something like "full silence" or "no messages tonight."
4. Resume normal operation the following morning.

**Example:**
> User: "off today"
> Bot: "Got it. I'll leave you alone today. See you tomorrow morning."

This command is explicitly designed for sick days, vacation days, days with a family emergency, or simply days when the user needs a break from being nudged.

---

## 6. Multi-User Considerations

The current system is designed for **a single primary user**. However, the architecture must not structurally prevent multi-user support, as the system may eventually be extended to a partner or shared with others if it proves effective.

**Immediate requirements (single user):**
- All state, preferences, and interaction logs are tied to a single user profile.
- No authentication layer is needed in Phase 1.

**Architectural requirements for future extensibility:**
- User identity must be tracked at the data model level from the start. All state records, interaction logs, and preferences should include a `user_id` field even if only one user exists initially.
- Configuration (schedules, energy defaults, Joplin API credentials, calendar tokens) should be stored per user, not as global constants.
- The bot should be capable of handling messages from multiple Discord users in the same server without confusion, even if it only actively serves one.

> If the system is later distributed or shared: multi-user support, access control, and per-user credential management will need to be revisited as a dedicated architectural step.

---

## 7. Context Pipeline

The bot's intelligence depends on having current awareness of the user's tasks and schedule. This section defines how that context is gathered and maintained.

### 7.1 Joplin Integration

| Aspect | Detail |
|--------|--------|
| Source | Joplin notes organized as checklists within project-based notebooks, synced to Dropbox. |
| Access Strategy (Primary) | Run Joplin Desktop (headless) on the home server, exposing the local REST API on port 41184. Poll periodically. |
| Access Strategy (Fallback) | Parse raw Joplin sync files from Dropbox directly. These are JSON/Markdown files with predictable structure. Less clean but avoids running Joplin as a service. **Start with primary; fallback is a contingency only.** |
| Sync Frequency | Every 15 minutes during active hours; every 60 minutes overnight. |
| Data Extracted | Note titles, checklist items (checked/unchecked), notebook names (as project/area identifiers), note modification timestamps. |
| Task Metadata | Inferred from notebook name (project/area), checklist position (priority proxy), and note content. User can tag tasks inline with `[high]`, `[low-energy]`, `[couch]`. |
| Write-back | Read-only in Phases 1 and 2. Write-back (mark tasks done) considered in Phase 3, with explicit per-action user confirmation. |

### 7.2 Calendar Integration

**Chosen strategy: Google Calendar as single integration point.**

All calendars (Outlook/work, AWS WorkMail, Proton) are consolidated into Google Calendar by the user prior to implementation. The bot integrates only with Google Calendar via the Google Calendar API (OAuth2, read-only scope). This avoids the complexity of multi-provider integration and leverages Google's well-documented API.

> **Pre-implementation task for user:** Ensure all relevant calendars are synced or mirrored into a single Google Calendar account before development begins.

| Aspect | Detail |
|--------|--------|
| Source | Google Calendar (consolidated from all calendar providers). |
| Access | Google Calendar API v3, OAuth2 authentication, read-only scope: `calendar.readonly`. |
| Sync Frequency | Every 10 minutes during active hours. Full day re-sync each morning at check-in time. |
| Data Extracted | Event title, start/end time, location, all-day flag. Used to compute free/busy windows. |
| Privacy | Event details are processed locally. Only titles and times are passed to the LLM context; attendee lists and event bodies are excluded by default. |

### 7.3 Internal State

The bot maintains a lightweight local state using **JSON files** (not a database, to simplify setup and keep the system portable). State includes:

- Last suggestion made and timestamp.
- User's response to last suggestion (accepted / skipped / completed / no response).
- Declared energy level if provided (defaults to time-based heuristic).
- Running log of interactions for end-of-day summary generation.
- Task queue: user-submitted tasks that don't yet live in Joplin.
- Morning routine completion status and declared energy level for the day.
- "Off today" flag (reset at midnight).
- `user_id` field on all records (for future multi-user extensibility).

### 7.4 Context Assembly

Before each LLM call, the bot assembles a context payload appropriate to the conversational situation:

**In Executive Function Assistant mode**, the payload includes:
- Current time and day of week (weekday vs. weekend).
- Today's calendar summary (meetings and free windows).
- Active tasks from Joplin (unchecked items, sorted by inferred priority).
- Recent interaction history (last 3–5 exchanges).
- Estimated energy level (time-based default, overridable by user).
- Any tasks in the local queue.
- Current mode (Work / Recovery / Weekend Recovery).

**In general conversational mode**, the payload may be nothing more than the actual user input.

This payload is included in the system prompt or user message to the Claude API.

### 7.5 Memory

The bot implements conversation memory beyond standard LLM context. In Phase 1, LLM context window memory is sufficient. From Phase 2 onward, a structured memory layer must support:

- Explicit `"remember this"` requests from the user — items must be recallable even after the conversation has moved on significantly.
- Persistent user preferences inferred from behavior (e.g., preferred working style, preferred task decomposition format).
- Per-user memory isolation (required for future multi-user support).

---

## 8. Technical Architecture

### 8.1 System Components

| Component | Technology | Role |
|-----------|-----------|------|
| Discord Bot | Python (`discord.py`) | Handles all user interaction via Discord. Sends proactive messages on schedule, responds to user commands and free-text input. |
| Scheduler | APScheduler (Python) | Manages timed events: check-ins, nudge windows, sync jobs. Runs within the bot process. |
| LLM Backend | Anthropic Claude API (Sonnet default, Opus on demand) | Generates all natural-language responses: nudges, decompositions, triage, check-in messages. Called on each interaction with assembled context. |
| Joplin Connector | HTTP client to Joplin REST API | Periodically fetches and parses task data from Joplin. |
| Calendar Connector | Google Calendar API v3 client | Fetches calendar events via OAuth2 token. |
| State Store | JSON files | Persists interaction log, user preferences, task queue, and session state. One file per logical domain (e.g., `state.json`, `interactions.json`, `memory.json`). |

### 8.2 Hosting

Self-hosted on the user's always-on, low-power Ubuntu PC. The bot runs as a `systemd` service for automatic startup and restart on boot. No cloud infrastructure required beyond the Discord and Anthropic API endpoints.

### 8.3 Discord Setup

A private Discord server with a single dedicated channel for bot interaction. The bot uses Discord's **button components** for quick responses (Accept / Skip / Done / Snooze) to minimize typing friction. **Rich embeds** are used for structured messages (daily briefings, task decompositions).

For high-priority nudges (e.g., approaching deadlines), the bot will `@mention` the user to trigger a mobile notification.

### 8.4 LLM Prompt Architecture

The bot uses a **system prompt** that defines its persona, operating mode, and behavioral rules. Each interaction appends the assembled context payload and the user's message (or a scheduler trigger description).

The system prompt includes:
- **Persona definition:** direct, warm, no fluff, no generic motivation. Concrete actions only.
- **Mode-specific instructions:** energy calibration, suggestion length, tone (Work vs. Recovery vs. Weekend).
- **Rules:** never guilt, always offer an exit, default to 15-minute commitments, always name the first physical action.
- **Context injection template** for tasks, calendar, and state.
- **Day type awareness:** weekday vs. weekend behavior.

---

## 9. User Configuration

All configuration is stored locally and editable via Discord commands or a simple config file.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `morning_routine` | `07:30` | Time the Morning Routine interview is initiated. |
| `morning_routine_retry_window_min` | `90` | Minutes after morning message before retry is sent if no response. |
| `work_start` | `09:15` | Start of Work Mode. Day Kick-off sent at this time. |
| `work_end` | `16:00` | End of Work Mode. Bot transitions toward Recovery. |
| `evening_start` | `20:30` | Start of Recovery Mode. Evening nudge sent at this time. |
| `bedtime` | `23:00` | Bedtime reminder sent at this time. Bot stops proactive messages after this. |
| `midday_checkin` | `13:00` | Time for the midday re-engagement check-in. |
| `end_of_day_review` | `22:30` | Optional end-of-day summary time. |
| `nudge_cooldown_min` | `45` | Minimum minutes between unsolicited nudges. |
| `min_gap_for_nudge_min` | `30` | Minimum free calendar gap to trigger a proactive nudge. |
| `joplin_api_port` | `41184` | Local port for Joplin REST API. |
| `low_energy_tags` | `[low-energy], [couch], [easy]` | Inline task tags that mark items as suitable for Recovery Mode. |
| `timezone` | `Europe/Berlin` | User's timezone for all scheduling. |
| `opus_session_max_messages` | `10` | Max messages before reverting from Opus to Sonnet. |
| `weekend_evening_nudge` | `true` | Whether to send an evening nudge on weekends. |

---

## 10. Example Interaction Flows

### 10.1 Morning Start

**Bot (07:30):**
> "Good morning. How was your night?"

**User:** "Acceptable, woke up twice for 5 minutes each time."

**Bot:** "How much sleep did you get?"

**User:** "5h37m"

**Bot:** "How energized do you feel?"

**User:** "Low-to-medium"

**Bot:** "Is there a particular task on your mind?"

**User:** "Nothing that I'd remember."

**Bot:** "Is there anything particularly urgent you must finish today?"

**User:** "An album for my kid she needs to hand in at kindergarten."

**Bot:** "Is there something else pressing you today?"

**User:** "Need to call the lawyer — it's not in the calendar, just remembered."

**Bot:** "How long are you expecting that call to be?"

**User:** "10 minutes."

**Bot:** "Alright. I'll look into your schedule and make some suggestions adapted to your energy level. For now: your most cognitively demanding agenda item is a Client Call at 14:00. Worth keeping that in mind so you don't burn yourself out beforehand."

---

### 10.2 Day Kick-off

**Bot (09:15):**
> Here's your day:
> - 09:00–10:00 — Team standup
> - 10:00–12:30 — Free (2.5 hours)
> - 12:30–13:30 — Lunch break
> - 14:00–15:00 — Client call
> - 15:00–16:00 — Free (1 hour)
>
> Your biggest open items: (1) Finalize QA docs [GoTrial, high], (2) Review PR for data pipeline, (3) Call insurance re: claim. That 10:00–12:30 block looks good for the QA docs. You mentioned yesterday evening you must look at the capacity planning for a new client.
>
> You mentioned this morning you need to call the lawyer. I'll suggest slotting that in at 13:50.
>
> What's the one thing that would make today feel productive?

**User:** "QA docs"

**Bot:** "Good call. After standup I'll ping you with the first step. It'll be small."

---

### 10.3 Nudges

**Bot (10:05):**
> "You wanted to look at the QA docs — still the plan? If yes: open Confluence, navigate to the QA section, open the first document and start reading. That's step one."

**User:** "Done, now what?"

**Bot:** *[provides next step]*

**Bot (13:45):**
> "If nothing's changed in your agenda, it's time to call the lawyer. Pick up your phone now and look up their contact. Let me know when you're there."

**User:** "I'm there, I found the contact."

**Bot:** "If you want advice on what to say, tell me the topic and I can suggest a short intro. If you're ready to go, just press CALL."

---

### 10.4 Evening Recovery

**Bot (20:30):**
> "Kids down? You've got about 2 hours. Here are two couch-friendly options:
> - Quick (~15 min): Reply to the 3 flagged emails in your inbox.
> - Medium (~30 min): Read through the PR diff for the data pipeline. Just reading, no coding needed.
>
> Or if tonight's a rest night, that's completely fine — I'll check in tomorrow morning."

**[Accept: Emails] [Accept: PR Review] [Rest Tonight]**

**User clicks:** [Accept: Emails]

**Bot:** "Great. Open your inbox, filter by flagged. Start with the shortest one. I'll check back in 20 minutes."

---

### 10.5 Post-Task Follow-up

**Bot (20:50):**
> "How did the emails go?"

**[Done] [Still working] [Skipped]**

**User clicks:** [Done]

**Bot:** "Nice. That's 3 fewer things on the list. Want to try the PR review while you're on a roll, or call it a night?"

---

### 10.6 Off Today

**User (any time):** "off today"

**Bot:** "Got it. I'll leave you alone today. See you tomorrow morning."

*[All proactive nudges suppressed for the rest of the day. Bedtime reminder still sent unless user specifies full silence.]*

---

### 10.7 Missed Morning Routine

*[User does not respond to the 07:30 Morning Routine message.]*

**Bot (09:00 — retry):**
> "Still there? Even just a quick check — how are you feeling this morning? One word is fine."

*[If still no response:]*

**Bot (09:15 — Day Kick-off using yesterday's baseline):**
> "Morning. Didn't hear from you earlier — no worries. Here's your day based on what I know: ..."
> *(Uses last known energy level and task priorities as context)*

---

### 10.8 Weekend Evening

**Bot (Saturday, 20:30):**
> "Evening. Nothing work-related tonight — but if you want to knock something small off the personal list:
> - Sort the stuff in the hallway (~15 min, couch-adjacent prep)
> - Read that article you bookmarked (~10 min)
>
> Or just a rest night. Completely valid."

---

## 11. Phased Delivery Plan

> Note: phasing may not yet fully reflect all interaction modes described above. Claude Code should flag gaps during implementation and surface them for prioritization.

### Phase 1: Foundation (MVP)

**Goal:** A working Discord bot that sends scheduled check-ins with calendar and task context, and responds to basic user messages.

1. Discord bot skeleton (`discord.py`) running as a `systemd` service on the home server.
2. Joplin REST API connector: fetch notebooks, notes, checklist items.
3. Google Calendar connector via Google Calendar API v3: fetch today's events.
4. Context assembly: merge tasks + calendar into a structured prompt payload.
5. Claude API integration: send context + trigger description, receive natural-language response.
6. Scheduled check-ins: morning routine, day kick-off, and evening check-in, with Discord button responses.
7. Basic state: JSON file logging of interactions and daily state.
8. Weekend mode: suppress work nudges on Saturday/Sunday; send evening nudge if `weekend_evening_nudge` is enabled.
9. "Off today" command: suppress all proactive messages for the rest of the calendar day.
10. Missed morning routine: retry logic and fallback to yesterday's baseline for Day Kick-off.

### Phase 2: Proactive Intelligence

**Goal:** The bot identifies opportunities and nudges without being asked.

1. Calendar gap detection: identify free windows and trigger nudges with task suggestions.
2. Deadline proximity alerts from Joplin note content (if dates are present).
3. Energy-aware mode switching: automatic Work/Recovery transition based on time and config.
4. Nudge cooldown logic: prevent over-messaging.
5. Task decomposition command: user references a task, bot returns concrete steps.
6. Follow-up loop: bot checks back after estimated task duration.
7. Midday check-in and end-of-day micro-review.
8. Structured memory layer: persistent `"remember this"` support.

### Phase 3: Refinement and Expansion

**Goal:** Learning from patterns, improving suggestion quality, and opening for multi-user use.

1. Interaction analytics: track acceptance/skip/completion rates by time of day and task type.
2. User-submitted task queue (add tasks via Discord that don't yet exist in Joplin).
3. Couch-compatibility inference: learn which tasks the user tends to do in the evening.
4. Configurable nudge personality (more/less assertive) based on user feedback.
5. Joplin write-back: mark tasks as done with explicit per-action confirmation.
6. Multi-user support: add authentication layer, per-user credential management, and isolation.
7. Adaptive learning: persist behavioral preferences inferred from interaction patterns.

---

## 12. Open Questions and Decisions

| # | Question | Decision / Status |
|---|----------|-------------------|
| 1 | Joplin access strategy | **Decided:** Use Joplin REST API (Option A). Fallback to Dropbox sync file parsing is a contingency, not the plan. |
| 2 | How to handle multi-device Joplin sync conflicts? | **Decided:** Bot is read-only. Task additions go to a local queue; user manually transfers to Joplin. |
| 3 | Should the bot write back to Joplin (mark tasks done)? | **Decided:** Not in Phase 1 or 2. Revisit in Phase 3 with explicit per-action confirmation. |
| 4 | Which calendar to integrate first? | **Decided:** Google Calendar. User will consolidate all calendars (Outlook, AWS WorkMail, Proton) into Google before implementation. |
| 5 | LLM model choice? | **Decided:** Claude Sonnet as default. Opus on demand via `<USE_OPUS>` tag, capped at `opus_session_max_messages` messages. |
| 6 | Discord notification behavior on mobile? | **Open:** User configures Discord notification settings per channel. Bot will `@mention` for high-priority nudges. Test mobile behavior early. |
| 7 | State storage format? | **Decided:** JSON files. No SQLite. Keeps setup simple and portable. |
| 8 | Weekend behavior? | **Decided:** Recovery Mode all day. No morning routine. No work nudges. Evening nudge enabled by default (configurable). |
| 9 | What happens if the user skips the Morning Routine? | **Decided:** One retry after 90 minutes. If still no response, Day Kick-off proceeds using yesterday's baseline state. |
| 10 | Can the user silence the bot for a day? | **Decided:** Yes. `"off today"` command suppresses all proactive messages for the remainder of the calendar day. Bedtime reminder still sent unless user specifies full silence. |
| 11 | Multi-user support? | **Decided:** Single user now. Architecture must include `user_id` on all state records from the start. Multi-user support is a Phase 3 feature. |

---

## 13. Success Criteria

The system will be considered successful if, after 2 weeks of use:

- The user engages with at least 60% of proactive nudges (accept, complete, or explicitly skip — not ignored).
- The user reports starting tasks more often in the evening compared to the pre-bot baseline.
- Morning check-ins are read and at least briefly responded to on most working days.
- The user does not find the bot annoying, intrusive, or guilt-inducing. If any of these occur, nudge frequency or tone must be adjusted.
- The user's subjective sense of "I know what I should be doing right now" improves.

---

## 14. Closing Remarks

- **The bot must be adaptive.** It learns not just user preferences but expected behavioral patterns. For example: if when decomposing a development task the user indicates they prefer working with an LLM (Claude Code), the bot must remember this and assume it going forward — not ask again.

- **Architecture must stay open.** The conversational architecture should leave room for more elaborate future operations: calendar blockers, draft creation, integration with other tools. These are not Phase 1 concerns, but they must not be structurally foreclosed.

- **Tone is a first-class feature.** The difference between a bot that helps and one that nags is almost entirely in phrasing and timing. Prompt engineering for tone (especially in Recovery Mode) should be treated with the same care as functional correctness.
