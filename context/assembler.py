"""C5 — Context Assembler.

Determines the current bot mode and energy level, then formats a
structured context string to pass to the LLM.

Pure functions (determine_mode, determine_energy) are module-level so
they are testable without instantiating ContextAssembler.

Standalone usage:
    docker compose exec bot python -m context.assembler
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from pathlib import Path
from typing import Optional

from config import Config
from connectors.calendar import compute_free_windows
from connectors.models import CalendarEvent, FreeWindow, Task
from state.manager import StateManager
from state.models import DailyState, Interaction
from utils.clock import Clock

log = logging.getLogger(__name__)


class Mode(str, Enum):
    MORNING = "morning"
    WORK = "work"
    RECOVERY = "recovery"
    WEEKEND = "weekend"
    GENERAL = "general"  # between work_end and evening_start


@dataclass
class AssembledContext:
    mode: Mode
    energy: str             # "low" | "medium-low" | "medium" | "high"
    now: datetime
    is_weekend: bool
    has_prior_history: bool
    tasks: list[Task]
    events: list[CalendarEvent]
    free_windows: list[FreeWindow]
    recent_interactions: list[Interaction]
    daily_state: DailyState
    text: str               # formatted context string for the LLM


# ── Pure helpers ──────────────────────────────────────────────────────────────

def determine_mode(now: datetime, config: Config) -> Mode:
    """Return the current operating mode based on weekday and time."""
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return Mode.WEEKEND

    t = now.time()
    if t < _hhmm_to_time(config.work_start):
        return Mode.MORNING
    if t < _hhmm_to_time(config.work_end):
        return Mode.WORK
    if t >= _hhmm_to_time(config.evening_start):
        return Mode.RECOVERY
    return Mode.GENERAL


def determine_energy(
    now: datetime,
    mode: Mode,
    declared_energy: Optional[str],
    config: Config,
) -> str:
    """Return the current energy level.

    Declared energy (from morning routine) overrides the time-based heuristic.

    Heuristic:
        RECOVERY / WEEKEND → low
        Within ±60 min of midday_checkin → medium-low
        Otherwise → medium
    """
    if declared_energy:
        return declared_energy

    if mode in (Mode.RECOVERY, Mode.WEEKEND):
        return "low"

    midday = _hhmm_to_time(config.midday_checkin)
    t = now.time()
    now_min = t.hour * 60 + t.minute
    midday_min = midday.hour * 60 + midday.minute
    if abs(now_min - midday_min) <= 60:
        return "medium-low"

    return "medium"


# ── Assembler ─────────────────────────────────────────────────────────────────

class ContextAssembler:
    """Assembles the full LLM context payload for a given moment.

    Args:
        config: Bot configuration.
        state_manager: For reading daily state and interaction history.
        clock: Clock instance (never call datetime.now() directly).
    """

    def __init__(self, config: Config, state_manager: StateManager, clock: Clock) -> None:
        self._config = config
        self._state = state_manager
        self._clock = clock

    async def assemble(
        self,
        tasks: list[Task],
        events: list[CalendarEvent],
        interactions: list[Interaction],
    ) -> AssembledContext:
        """Assemble context from pre-fetched data and current state.

        Callers are responsible for fetching tasks, events, and interactions
        from their respective connectors before calling this method.
        """
        now = self._clock.now()
        daily = await self._state.get_daily()
        has_prior = await self._state.has_previous_daily()

        mode = determine_mode(now, self._config)
        energy = determine_energy(now, mode, daily.get("declared_energy"), self._config)

        work_start = _hhmm_to_datetime(now, self._config.work_start)
        work_end = _hhmm_to_datetime(now, self._config.work_end)
        free_windows = compute_free_windows(
            events, work_start, work_end, self._config.min_gap_for_nudge_min
        )

        text = _format_context(
            now=now,
            mode=mode,
            energy=energy,
            has_prior_history=has_prior,
            tasks=tasks,
            events=events,
            free_windows=free_windows,
            interactions=interactions,
            daily=daily,
            config=self._config,
        )

        return AssembledContext(
            mode=mode,
            energy=energy,
            now=now,
            is_weekend=now.weekday() >= 5,
            has_prior_history=has_prior,
            tasks=tasks,
            events=events,
            free_windows=free_windows,
            recent_interactions=interactions,
            daily_state=daily,
            text=text,
        )


# ── Context formatting ────────────────────────────────────────────────────────

def _format_context(
    *,
    now: datetime,
    mode: Mode,
    energy: str,
    has_prior_history: bool,
    tasks: list[Task],
    events: list[CalendarEvent],
    free_windows: list[FreeWindow],
    interactions: list[Interaction],
    daily: DailyState,
    config: Config,
) -> str:
    weekday = now.strftime("%A")
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    lines = [
        f"=== EVA Context | {weekday}, {date_str} {time_str} | Mode: {mode.value} | Energy: {energy} ===",
        "",
    ]

    # Calendar
    lines.append("TODAY'S CALENDAR")
    timed = [e for e in events if not e.is_all_day]
    all_day = [e for e in events if e.is_all_day]
    if all_day or timed:
        for e in all_day:
            lines.append(f"  [all-day]          {e.title}")
        for e in timed:
            if e.end <= now:
                label = "[past]    "
            elif e.start <= now:
                label = "[now]     "
            else:
                label = "[upcoming]"
            lines.append(f"  {label} {e.start.strftime('%H:%M')}–{e.end.strftime('%H:%M')}  {e.title}")
    else:
        lines.append("  (no events today)")

    if free_windows:
        fw_parts = [
            f"{w.start.strftime('%H:%M')}–{w.end.strftime('%H:%M')} ({w.duration_min} min)"
            for w in free_windows
        ]
        lines.append(f"  Free windows: {', '.join(fw_parts)}")
    else:
        lines.append("  Free windows: none")
    lines.append("")

    # Tasks (high-priority first, then by notebook)
    lines.append("ACTIVE TASKS")
    if tasks:
        sorted_tasks = sorted(
            tasks,
            key=lambda t: (not t.is_high_priority, t.notebook, t.position),
        )
        current_nb = None
        for task in sorted_tasks[:20]:  # cap to avoid bloating context
            if task.notebook != current_nb:
                current_nb = task.notebook
                lines.append(f"  [{current_nb}]")
            tag_str = " ".join(task.tags)
            suffix = f"  {tag_str}" if tag_str else ""
            lines.append(f"    {task.title}{suffix}")
    else:
        lines.append("  (no active tasks)")
    lines.append("")

    # State
    lines.append("STATE")
    morning_status = "complete" if daily.get("morning_complete") else "pending"
    lines.append(f"  Morning routine: {morning_status}")
    if daily.get("off_today"):
        lines.append("  Off today: YES — suppress all proactive messages")
    if daily.get("last_suggestion"):
        lines.append(f"  Last suggestion: \"{daily['last_suggestion']}\"")
    if not has_prior_history:
        lines.append("  Note: first session — do not reference prior days.")
    lines.append("===")

    return "\n".join(lines)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _hhmm_to_time(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def _hhmm_to_datetime(now: datetime, hhmm: str) -> datetime:
    t = _hhmm_to_time(hhmm)
    return now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)


# ── Standalone verification ───────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from config import load_config
    from connectors.calendar import CalendarConnector
    from connectors.joplin import JoplinConnector
    from state.manager import StateManager
    from utils.clock import RealClock

    _TOKEN_PATH = Path("secrets/google_token.json")

    async def _main() -> None:
        config = load_config()
        clock = RealClock(config.timezone)
        state_manager = StateManager(clock=clock)
        await state_manager.initialize()

        joplin = JoplinConnector(
            host=config.joplin_host,
            port=config.joplin_api_port,
            token=config.joplin_api_token,
        )
        calendar = CalendarConnector(
            token_path=_TOKEN_PATH,
            timezone=config.timezone,
            excluded_calendar_ids=config.excluded_calendar_ids,
            min_gap_min=config.min_gap_for_nudge_min,
        )

        tasks, events, interactions = await asyncio.gather(
            joplin.get_tasks(),
            calendar.get_events(),
            state_manager.get_recent_interactions(5),
        )

        assembler = ContextAssembler(config=config, state_manager=state_manager, clock=clock)
        ctx = await assembler.assemble(tasks=tasks, events=events, interactions=interactions)

        print(ctx.text)
        print(f"\nMode: {ctx.mode.value}  |  Energy: {ctx.energy}  |  "
              f"Tasks: {len(ctx.tasks)}  |  Events: {len(ctx.events)}  |  "
              f"Free windows: {len(ctx.free_windows)}")

    asyncio.run(_main())
