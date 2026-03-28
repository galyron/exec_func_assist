# Parking Lot

Deferred features and design decisions that are not in scope for the current phase but have been thought through sufficiently to guide implementation when the time comes. Each entry includes the motivation, the target design, and a clear implementation path so that no research needs to be repeated.

---

## PL-0 -- Gamification

TO BE DEVELOPED

Core idea: maintain a score/streak of completed tasks for each day
Goal could be to be above a given level every day bot for minor and major tasks as well as for chores
There could be a small dashboard that shows stats, time series, etc., like a health up that tracks activity

## PL-1 — Configurable nudge windows and profiles

### Motivation

The current system classifies time into three hardcoded modes: **Work**, **Recovery**, and **Weekend**. This is sufficient for Phase 1 but makes it difficult to refine nudge behaviour later without touching multiple files. Desired future capability: independently configuring intensity, tone, energy assumption, and timing for any named time window — e.g. treating Friday evening differently from Monday evening, or Saturday morning differently from Saturday evening — without writing code.

### Current limitation

Mode is resolved in `context/assembler.py` via a chain of `if/elif` comparisons against config times. The mode enum is then used in `llm/prompts.py` to select a system prompt template. Adding a new window (e.g. `friday-evening`) today would require changes in the assembler, the scheduler, the prompt selector, and potentially the config schema.

### Target design

Separate three concerns that are currently tangled:

**1. Day type classification**

A `DayType` is a named category for a calendar day. Resolved once per day at midnight (or at bot startup).

```
weekday          Monday–Thursday
friday           Friday (often different energy by evening)
saturday         Saturday
sunday           Sunday
```

Day types are fixed — they map directly to `datetime.weekday()` and require no configuration.

**2. Time window definition (config-driven)**

A `TimeWindow` is a named slot within a day type, defined in `config.json`. Each window specifies when it applies and which nudge profile governs it.

```json
"time_windows": [
  {
    "name": "weekday-morning",
    "day_types": ["weekday", "friday"],
    "start": "06:00",
    "end": "work_start",
    "profile": "morning"
  },
  {
    "name": "weekday-work",
    "day_types": ["weekday", "friday"],
    "start": "work_start",
    "end": "work_end",
    "profile": "work"
  },
  {
    "name": "weekday-evening",
    "day_types": ["weekday"],
    "start": "evening_start",
    "end": "bedtime",
    "profile": "recovery"
  },
  {
    "name": "friday-evening",
    "day_types": ["friday"],
    "start": "evening_start",
    "end": "bedtime",
    "profile": "friday-recovery"
  },
  {
    "name": "saturday-morning",
    "day_types": ["saturday"],
    "start": "08:00",
    "end": "13:00",
    "profile": "weekend-gentle"
  },
  {
    "name": "weekend-evening",
    "day_types": ["saturday", "sunday"],
    "start": "evening_start",
    "end": "bedtime",
    "profile": "recovery"
  }
]
```

`start` and `end` values can be either a literal `"HH:MM"` time or a reference to a named config parameter (e.g. `"work_start"`). Windows must not overlap within a day type. A `default` window covers any unmatched time (e.g. overnight).

**3. Nudge profile (config-driven)**

A `NudgeProfile` defines the behaviour that applies within a time window. Profiles are named and reusable across windows.

```json
"nudge_profiles": {
  "work": {
    "intensity": 4,
    "default_energy": "medium",
    "tone": "assertive",
    "proactive_nudges_enabled": true,
    "couch_compatible_only": false,
    "max_suggestion_duration_min": 90,
    "commitment_duration_min": 15
  },
  "recovery": {
    "intensity": 2,
    "default_energy": "low",
    "tone": "gentle",
    "proactive_nudges_enabled": true,
    "couch_compatible_only": true,
    "max_suggestion_duration_min": 30,
    "commitment_duration_min": 15
  },
  "friday-recovery": {
    "intensity": 1,
    "default_energy": "low",
    "tone": "gentle",
    "proactive_nudges_enabled": false,
    "couch_compatible_only": true,
    "max_suggestion_duration_min": 20,
    "commitment_duration_min": 15
  },
  "weekend-gentle": {
    "intensity": 1,
    "default_energy": "low",
    "tone": "gentle",
    "proactive_nudges_enabled": false,
    "couch_compatible_only": true,
    "max_suggestion_duration_min": 20,
    "commitment_duration_min": 15
  }
}
```

Profile fields:

| Field | Type | Meaning |
|-------|------|---------|
| `intensity` | 1–5 | How assertive the bot is. 5 = strong push, 1 = barely a whisper. Injected into the system prompt. |
| `default_energy` | string | Energy level assumed when the user hasn't declared one for the day. |
| `tone` | string | System prompt tone variant: `"assertive"` \| `"gentle"` \| `"neutral"` |
| `proactive_nudges_enabled` | bool | Whether the bot sends unsolicited nudges during this window. |
| `couch_compatible_only` | bool | Whether task suggestions are restricted to `[couch]`/`[low-energy]`-tagged items. |
| `max_suggestion_duration_min` | int | Cap on suggested task duration. |
| `commitment_duration_min` | int | Default "try this for N minutes" contract. |

### Implementation path

When implementing this feature, the changes are localised and additive:

1. **`config.py`**: Add `time_windows: list[TimeWindowConfig]` and `nudge_profiles: dict[str, NudgeProfile]` to the `Config` dataclass. Validate that windows don't overlap and all referenced profiles exist.

2. **`context/window.py`** *(new)*: `WindowResolver` class with a single method `resolve(now: datetime) -> tuple[str, NudgeProfile]`. Returns the active window name and its profile. This replaces the `if/elif` mode chain in the assembler.

3. **`context/assembler.py`**: Replace `mode: Mode` enum with `window: str` and `profile: NudgeProfile`. Pass the profile fields (intensity, tone, energy default) into the context payload.

4. **`llm/prompts.py`**: Replace mode-keyed template lookup with profile-driven prompt construction. `intensity` and `tone` are injected as variables into the system prompt template, not as separate template files.

5. **`scheduler.py`**: Replace the current weekend/weekday job split with profile-driven job registration. A window with `proactive_nudges_enabled: false` simply does not register nudge jobs for that window.

6. **`config.example.json`**: Add the `time_windows` and `nudge_profiles` sections with sensible defaults that reproduce current Phase 1 behaviour exactly.

### Backwards compatibility

Phase 1 code uses a `Mode` enum (`WORK`, `RECOVERY`, `WEEKEND`). When implementing this feature, `Mode` is retired. The migration is: existing behaviour is reproduced by the default `time_windows` + `nudge_profiles` config. No state migration needed — `mode` is a runtime concept, not persisted.

### When to implement

Phase 2 or Phase 3, after at least two weeks of real usage. The current three-mode system may be entirely sufficient; this should only be built if the user finds they want finer-grained control that isn't achievable by adjusting the existing config parameters.

---
