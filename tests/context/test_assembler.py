"""Tests for C5 — Context Assembler.

determine_mode and determine_energy are pure functions tested exhaustively.
ContextAssembler.assemble is tested with mocked state.
"""

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from context.assembler import (
    ContextAssembler,
    Mode,
    determine_energy,
    determine_mode,
)
from connectors.models import Task

TZ = ZoneInfo("Europe/Berlin")

# 2026-03-23 is a Monday
_BASE_DATE = date(2026, 3, 23)


def dt(weekday: int, hour: int, minute: int = 0) -> datetime:
    """Return a timezone-aware datetime on a given weekday (0=Mon, 5=Sat, 6=Sun)."""
    d = _BASE_DATE + timedelta(days=weekday)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=TZ)


@pytest.fixture
def config():
    cfg = MagicMock()
    cfg.work_start = "09:15"
    cfg.work_end = "16:00"
    cfg.evening_start = "20:30"
    cfg.midday_checkin = "13:00"
    cfg.min_gap_for_nudge_min = 30
    cfg.user_name = "Gabriell"
    return cfg


@pytest.fixture
def daily_state():
    return {
        "date": "2026-03-23",
        "morning_complete": False,
        "morning_questions_asked": [],
        "declared_energy": None,
        "off_today": False,
        "off_today_full_silence": False,
        "task_queue": [],
        "opus_session_active": False,
        "opus_session_messages": 0,
        "last_suggestion": None,
        "last_suggestion_ts": None,
    }


@pytest.fixture
def state_manager(daily_state):
    sm = MagicMock()
    sm.get_daily = AsyncMock(return_value=daily_state)
    sm.has_previous_daily = AsyncMock(return_value=False)
    return sm


@pytest.fixture
def clock():
    c = MagicMock()
    c.now.return_value = dt(0, 10, 0)  # Monday 10:00 → WORK
    return c


@pytest.fixture
def assembler(config, state_manager, clock):
    return ContextAssembler(config=config, state_manager=state_manager, clock=clock)


# ── determine_mode ────────────────────────────────────────────────────────────

def test_mode_morning_before_work_start(config):
    assert determine_mode(dt(0, 8, 0), config) == Mode.MORNING


def test_mode_work_during_hours(config):
    assert determine_mode(dt(0, 11, 0), config) == Mode.WORK


def test_mode_work_at_exact_start(config):
    assert determine_mode(dt(0, 9, 15), config) == Mode.WORK


def test_mode_general_between_work_end_and_evening(config):
    assert determine_mode(dt(0, 17, 0), config) == Mode.GENERAL


def test_mode_recovery_after_evening_start(config):
    assert determine_mode(dt(0, 21, 0), config) == Mode.RECOVERY


def test_mode_recovery_at_exact_evening_start(config):
    assert determine_mode(dt(0, 20, 30), config) == Mode.RECOVERY


def test_mode_weekend_saturday(config):
    assert determine_mode(dt(5, 10, 0), config) == Mode.WEEKEND


def test_mode_weekend_sunday(config):
    assert determine_mode(dt(6, 10, 0), config) == Mode.WEEKEND


# ── determine_energy ──────────────────────────────────────────────────────────

def test_energy_low_in_recovery(config):
    assert determine_energy(dt(0, 21, 0), Mode.RECOVERY, None, config) == "low"


def test_energy_low_on_weekend(config):
    assert determine_energy(dt(5, 10, 0), Mode.WEEKEND, None, config) == "low"


def test_energy_medium_low_at_midday(config):
    # 13:00 is midday_checkin — within ±60 min → medium-low
    assert determine_energy(dt(0, 13, 0), Mode.WORK, None, config) == "medium-low"


def test_energy_medium_low_within_window(config):
    assert determine_energy(dt(0, 13, 45), Mode.WORK, None, config) == "medium-low"


def test_energy_medium_outside_lunch_window(config):
    # 10:00 is more than 60 min from 13:00
    assert determine_energy(dt(0, 10, 0), Mode.WORK, None, config) == "medium"


def test_declared_energy_overrides_heuristic(config):
    assert determine_energy(dt(0, 21, 0), Mode.RECOVERY, "high", config) == "high"


def test_declared_energy_overrides_weekend(config):
    assert determine_energy(dt(5, 10, 0), Mode.WEEKEND, "medium", config) == "medium"


# ── ContextAssembler.assemble ─────────────────────────────────────────────────

async def test_assemble_returns_work_mode(assembler):
    ctx = await assembler.assemble(tasks=[], events=[], interactions=[])
    assert ctx.mode == Mode.WORK


async def test_assemble_energy_medium_at_10am(assembler):
    ctx = await assembler.assemble(tasks=[], events=[], interactions=[])
    assert ctx.energy == "medium"


async def test_assemble_no_prior_history_flag(assembler):
    ctx = await assembler.assemble(tasks=[], events=[], interactions=[])
    assert ctx.has_prior_history is False


async def test_assemble_is_not_weekend_on_monday(assembler):
    ctx = await assembler.assemble(tasks=[], events=[], interactions=[])
    assert ctx.is_weekend is False


async def test_assemble_text_contains_mode(assembler):
    ctx = await assembler.assemble(tasks=[], events=[], interactions=[])
    assert "work" in ctx.text.lower()


async def test_assemble_text_contains_calendar_section(assembler):
    ctx = await assembler.assemble(tasks=[], events=[], interactions=[])
    assert "CALENDAR" in ctx.text


async def test_assemble_text_contains_tasks_section(assembler):
    ctx = await assembler.assemble(tasks=[], events=[], interactions=[])
    assert "TASKS" in ctx.text


async def test_assemble_task_appears_in_text(assembler):
    tasks = [Task(
        id="t1", title="Fix critical bug", notebook="Work", notebook_id="nb1",
        tags=["[high]"], is_high_priority=True, position=0, updated_time=0,
    )]
    ctx = await assembler.assemble(tasks=tasks, events=[], interactions=[])
    assert "Fix critical bug" in ctx.text


async def test_assemble_first_run_note_in_text(assembler):
    ctx = await assembler.assemble(tasks=[], events=[], interactions=[])
    assert "first session" in ctx.text


async def test_assemble_weekend_mode_and_low_energy(config, state_manager):
    clock = MagicMock()
    clock.now.return_value = dt(5, 10, 0)  # Saturday
    a = ContextAssembler(config=config, state_manager=state_manager, clock=clock)
    ctx = await a.assemble(tasks=[], events=[], interactions=[])
    assert ctx.mode == Mode.WEEKEND
    assert ctx.is_weekend is True
    assert ctx.energy == "low"


async def test_assemble_with_prior_history(config, clock):
    sm = MagicMock()
    sm.get_daily = AsyncMock(return_value={
        "date": "2026-03-23", "morning_complete": True,
        "morning_questions_asked": [], "declared_energy": None,
        "off_today": False, "off_today_full_silence": False,
        "task_queue": [], "opus_session_active": False,
        "opus_session_messages": 0, "last_suggestion": None, "last_suggestion_ts": None,
    })
    sm.has_previous_daily = AsyncMock(return_value=True)
    a = ContextAssembler(config=config, state_manager=sm, clock=clock)
    ctx = await a.assemble(tasks=[], events=[], interactions=[])
    assert ctx.has_prior_history is True
    assert "first session" not in ctx.text
