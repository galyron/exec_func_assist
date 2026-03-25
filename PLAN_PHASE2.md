# Phase 2 — Write-Back Capabilities

## Goal

Enable EVA to close the loop on tasks and calendar: mark Joplin todos as done,
create new Joplin tasks, and create Google Calendar events from natural language.

---

## Phase A — Joplin Write-Back

**No OAuth changes required.** Joplin uses a simple API token already in `.env`.

### A1 — Joplin connector write methods

Add to `connectors/joplin.py`:

- `mark_done(note_id: str) -> bool`
  - Standalone todos: `PUT /notes/:id` with `{"todo_completed": <unix_ms>}`
  - Checklist items: fetch note body, replace `- [ ] <text>` with `- [x] <text>`, `PUT /notes/:id`
  - The `Task` model already carries `note_id` and `is_checklist_item` / `checklist_item_text`
    fields — confirm these are present or add them.

- `create_task(title: str) -> str` (returns new note id)
  - `POST /notes` with `{"title": title, "is_todo": 1, "parent_id": "<00_TODO folder id>"}`
  - Folder id is cached from `get_tasks()` already — expose it or re-fetch.

### A2 — `add: <task>` writes to Joplin

Currently `add:` appends to `state.json` local queue only. Change `_handle_add_task` in
`handlers/on_demand.py` to call `joplin.create_task(title)` instead.
Keep the local queue as a fallback if Joplin is unavailable.

Requires: inject `JoplinConnector` into `OnDemandHandler`.

### A3 — `DONE_TASK` intent: mark a specific task done

New intent `DONE_TASK` detected by `done: <text>` prefix (distinct from plain `done`
which stays as the FINISHED acknowledgement).

Flow:
1. Fetch current task list from Joplin.
2. Pass task list + user text to LLM → LLM returns the best-matching `note_id`
   (structured output or ask it to return just the ID).
3. Call `joplin.mark_done(note_id)`.
4. Confirm to user: "Marked **<title>** as done."

### A4 — Plain `done` auto-completes last suggested task

When `FINISHED` fires and `state.daily.last_suggested_task_id` is set, call
`joplin.mark_done(last_suggested_task_id)` automatically.
Store `last_suggested_task_id` in daily state whenever the bot suggests a specific task.

### A5 — Tests for A1–A4

Unit tests for:
- `mark_done` (both standalone todo and checklist item paths)
- `create_task`
- `detect_intent` recognises `done: <text>` as `DONE_TASK`
- `_handle_add_task` calls connector instead of local queue
- `_handle_done_task` calls `mark_done` with LLM-resolved id

---

## Phase B — Google Calendar Write

**Requires OAuth re-auth** (scope expansion from `calendar.readonly` to
`https://www.googleapis.com/auth/calendar.events`).

### B1 — OAuth scope expansion

- Update `setup_calendar.py` to request `calendar.events` scope.
- User re-runs OAuth on MacBook, copies new `google_token.json` to mbox.
- Document in README.

### B2 — Calendar connector write method

Add to `connectors/calendar.py`:

- `create_event(title: str, start: datetime, end: datetime, calendar_id: str = "primary") -> str`
  - Calls `events.insert(calendarId=calendar_id, body={...})`.
  - Returns the new event id.

### B3 — `ADD_EVENT` intent

New intent `ADD_EVENT` detected by `schedule:` or `add event:` prefix.

Flow:
1. Pass raw text to LLM with a structured extraction prompt: extract `title`, `date`,
   `start_time`, `duration_min`, `calendar_id` (default `primary`).
2. LLM returns JSON fields (use a dedicated small extraction call, not the main context call).
3. Validate fields (date parseable, duration > 0).
4. Call `calendar.create_event(...)`.
5. Confirm to user: "Added **<title>** to your calendar on <date> at <time>."

### B4 — Tests for B1–B3

Unit tests for:
- `create_event` (mock the Google API client)
- `detect_intent` recognises `schedule:` / `add event:` as `ADD_EVENT`
- `_handle_add_event` calls connector with LLM-extracted fields

---

## Sequencing

```
A1 (connector write methods)
  └─▶ A2 (add: → Joplin)
  └─▶ A3 (done: intent)
  └─▶ A4 (auto-complete last suggestion)
A5 (tests) ← depends on A1–A4

B1 (OAuth scope)
  └─▶ B2 (connector write method)
        └─▶ B3 (ADD_EVENT intent)
B4 (tests) ← depends on B2–B3
```

A and B are independent — A can ship before B1 OAuth re-auth is done.

---

## State additions

`daily_state` needs one new field:

```json
"last_suggested_task_id": null   // Joplin note_id of most recently suggested task
```

`Task` model may need two new fields (check `connectors/models.py`):

```python
note_id: str           # Joplin note ID — needed for write-back
checklist_item_text: str | None   # if from a checklist, the raw line text for matching
```

---

## Open questions (decide before B3)

- Which calendar should `ADD_EVENT` default to? (`primary`, or let the user pick?)
- Should `schedule:` also add a task to Joplin, or just the calendar, or both?
- Duration default when user doesn't specify? (suggest: 60 min)
