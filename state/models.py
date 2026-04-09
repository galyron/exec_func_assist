"""State schema definitions.

TypedDicts define the shape of each JSON state file. All records include
user_id for future multi-user extensibility (D6 / spec §6).
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict


class DailyState(TypedDict):
    """State that resets each calendar day."""
    date: str                           # ISO date: "2026-03-24"
    morning_complete: bool
    morning_questions_asked: list[str]  # question keys already sent
    declared_energy: Optional[str]      # "low" | "medium-low" | "medium" | "high" | None
    off_today: bool
    off_today_full_silence: bool
    task_queue: list[dict[str, Any]]    # user-added tasks not yet in Joplin
    opus_session_active: bool
    opus_session_messages: int
    last_suggestion: Optional[str]      # text of last suggestion sent
    last_suggestion_ts: Optional[str]   # ISO datetime
    last_suggested_task_id: Optional[str]  # Joplin task id of last suggested task (for auto-done)
    commitment_minutes: Optional[int]   # duration of current commitment timer (None = not set)
    morning_retry_sent: bool            # True once fire_retry has fired — prevents duplicate sends
    reminders: list[dict[str, Any]]     # active timed reminders [{id, text, fire_at, created_at}]
    reminder_counter: int               # monotonic counter for unique reminder job IDs
    last_nudge_ts: Optional[str]        # ISO datetime of last proactive nudge (for cooldown)


class PreviousDailyState(TypedDict):
    """Snapshot of the previous day's DailyState, kept for fallback."""
    date: str
    declared_energy: Optional[str]
    task_queue: list[dict[str, Any]]
    morning_complete: bool


class MonthlySpend(TypedDict):
    """Running Anthropic API spend for a calendar month."""
    month: str          # "YYYY-MM"
    usd: float


class BotState(TypedDict):
    """Root state.json structure."""
    user_id: str
    first_run_completed: bool
    daily: DailyState
    previous_daily: Optional[PreviousDailyState]
    monthly_spend: MonthlySpend


class Interaction(TypedDict):
    """A single logged exchange."""
    timestamp: str      # ISO datetime
    direction: str      # "bot" | "user"
    content: str
    mode: str           # "morning" | "work" | "recovery" | "weekend" | "general"


class InteractionLog(TypedDict):
    """Root interactions.json structure."""
    user_id: str
    interactions: list[Interaction]


class MemoryStore(TypedDict):
    """Root memory.json structure. Populated in Phase 2."""
    user_id: str
    memories: list[dict[str, Any]]


# ── Default constructors ───────────────────────────────────────────────────────

def default_daily_state(date_str: str) -> DailyState:
    return DailyState(
        date=date_str,
        morning_complete=False,
        morning_questions_asked=[],
        declared_energy=None,
        off_today=False,
        off_today_full_silence=False,
        task_queue=[],
        opus_session_active=False,
        opus_session_messages=0,
        last_suggestion=None,
        last_suggestion_ts=None,
        last_suggested_task_id=None,
        commitment_minutes=None,
        morning_retry_sent=False,
        reminders=[],
        reminder_counter=0,
        last_nudge_ts=None,
    )


def default_bot_state(date_str: str) -> BotState:
    from datetime import date
    return BotState(
        user_id="default",
        first_run_completed=False,
        daily=default_daily_state(date_str),
        previous_daily=None,
        monthly_spend=MonthlySpend(month=date_str[:7], usd=0.0),
    )


def default_interaction_log() -> InteractionLog:
    return InteractionLog(user_id="default", interactions=[])


def default_memory_store() -> MemoryStore:
    return MemoryStore(user_id="default", memories=[])
