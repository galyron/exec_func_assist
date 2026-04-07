"""Tests for C2 — State Manager."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from state.manager import StateManager
from state.models import Interaction
from utils.clock import DebugClock, RealClock

TZ = ZoneInfo("Europe/Berlin")


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_clock(date_str: str, hour: int = 9, minute: int = 0) -> DebugClock:
    """Return a frozen DebugClock at the given date/time."""
    dt = datetime.strptime(f"{date_str} {hour:02d}:{minute:02d}", "%Y-%m-%d %H:%M")
    return DebugClock(start_time=dt.replace(tzinfo=TZ), multiplier=0.0)


@pytest.fixture
def clock_today():
    return make_clock("2026-03-24")


@pytest.fixture
def manager(tmp_path, clock_today):
    return StateManager(data_dir=tmp_path, clock=clock_today)


# ── Initialization ────────────────────────────────────────────────────────────

async def test_initialize_creates_data_dir(tmp_path, clock_today):
    data_dir = tmp_path / "data"
    mgr = StateManager(data_dir=data_dir, clock=clock_today)
    await mgr.initialize()
    assert data_dir.exists()


async def test_initialize_creates_state_files(manager, tmp_path):
    await manager.initialize()
    assert (tmp_path / "state.json").exists()
    assert (tmp_path / "interactions.json").exists()
    assert (tmp_path / "memory.json").exists()


async def test_initialize_sets_first_run_false_by_default(manager):
    await manager.initialize()
    assert await manager.is_first_run() is True


async def test_initialize_idempotent(manager):
    await manager.initialize()
    await manager.initialize()  # second call should not raise or corrupt
    state = await manager.load_state()
    assert state["daily"]["date"] == "2026-03-24"


# ── First run detection ───────────────────────────────────────────────────────

async def test_is_first_run_true_before_mark(manager):
    await manager.initialize()
    assert await manager.is_first_run() is True


async def test_mark_first_run_complete(manager):
    await manager.initialize()
    await manager.mark_first_run_complete()
    assert await manager.is_first_run() is False


# ── Daily state ───────────────────────────────────────────────────────────────

async def test_daily_state_initialised_correctly(manager):
    await manager.initialize()
    daily = await manager.get_daily()
    assert daily["date"] == "2026-03-24"
    assert daily["morning_complete"] is False
    assert daily["off_today"] is False
    assert daily["declared_energy"] is None
    assert daily["task_queue"] == []


async def test_update_daily_field(manager):
    await manager.initialize()
    await manager.update_daily(off_today=True)
    daily = await manager.get_daily()
    assert daily["off_today"] is True


async def test_update_multiple_daily_fields(manager):
    await manager.initialize()
    await manager.update_daily(morning_complete=True, declared_energy="medium")
    daily = await manager.get_daily()
    assert daily["morning_complete"] is True
    assert daily["declared_energy"] == "medium"


async def test_update_unknown_field_raises(manager):
    await manager.initialize()
    with pytest.raises(KeyError):
        await manager.update_daily(nonexistent_field="oops")


# ── Daily rollover ────────────────────────────────────────────────────────────

async def test_rollover_on_new_day(tmp_path):
    clock_yesterday = make_clock("2026-03-23")
    mgr = StateManager(data_dir=tmp_path, clock=clock_yesterday)
    await mgr.initialize()
    await mgr.update_daily(declared_energy="high", morning_complete=True)

    # Simulate bot restarting the next day
    clock_today = make_clock("2026-03-24")
    mgr2 = StateManager(data_dir=tmp_path, clock=clock_today)
    await mgr2.initialize()

    daily = await mgr2.get_daily()
    assert daily["date"] == "2026-03-24"
    assert daily["morning_complete"] is False  # reset

    state = await mgr2.load_state()
    assert state["previous_daily"] is not None
    assert state["previous_daily"]["date"] == "2026-03-23"
    assert state["previous_daily"]["declared_energy"] == "high"


async def test_has_previous_daily_false_on_first_run(manager):
    await manager.initialize()
    assert await manager.has_previous_daily() is False


async def test_has_previous_daily_true_after_rollover(tmp_path):
    clock_yesterday = make_clock("2026-03-23")
    mgr = StateManager(data_dir=tmp_path, clock=clock_yesterday)
    await mgr.initialize()

    clock_today = make_clock("2026-03-24")
    mgr2 = StateManager(data_dir=tmp_path, clock=clock_today)
    await mgr2.initialize()

    assert await mgr2.has_previous_daily() is True


# ── Interaction log ───────────────────────────────────────────────────────────

async def test_append_and_retrieve_interaction(manager):
    await manager.initialize()
    interaction: Interaction = {
        "timestamp": "2026-03-24T09:00:00",
        "direction": "bot",
        "content": "Good morning.",
        "mode": "morning",
    }
    await manager.append_interaction(interaction)
    recent = await manager.get_recent_interactions(n=5)
    assert len(recent) == 1
    assert recent[0]["content"] == "Good morning."


async def test_get_recent_interactions_limits_results(manager):
    await manager.initialize()
    for i in range(10):
        await manager.append_interaction({
            "timestamp": f"2026-03-24T09:{i:02d}:00",
            "direction": "bot",
            "content": f"Message {i}",
            "mode": "general",
        })
    recent = await manager.get_recent_interactions(n=3)
    assert len(recent) == 3
    assert recent[-1]["content"] == "Message 9"


async def test_get_today_interactions_filters_by_date(manager):
    await manager.initialize()
    await manager.append_interaction({
        "timestamp": "2026-03-23T22:00:00",
        "direction": "user", "content": "yesterday", "mode": "general",
    })
    await manager.append_interaction({
        "timestamp": "2026-03-24T09:00:00",
        "direction": "bot", "content": "today", "mode": "work",
    })
    today_only = await manager.get_today_interactions()
    assert len(today_only) == 1
    assert today_only[0]["content"] == "today"


# ── Atomic write ──────────────────────────────────────────────────────────────

async def test_no_tmp_file_left_after_write(manager):
    await manager.initialize()
    tmp_file = manager._state_path.with_suffix(".tmp")
    assert not tmp_file.exists()


# ── Automatic rollover on access (no restart needed) ─────────────────────────

async def test_get_daily_rolls_over_on_date_change(tmp_path):
    """get_daily() should auto-rollover when the clock date changes, even without restart."""
    clock = make_clock("2026-03-23")
    mgr = StateManager(data_dir=tmp_path, clock=clock)
    await mgr.initialize()
    await mgr.update_daily(off_today=True, morning_complete=True)

    # Advance the clock to the next day (same manager instance, no restart)
    clock._start_sim = datetime(2026, 3, 24, 9, 0, tzinfo=TZ)

    daily = await mgr.get_daily()
    assert daily["date"] == "2026-03-24"
    assert daily["off_today"] is False  # reset
    assert daily["morning_complete"] is False  # reset


async def test_update_daily_rolls_over_on_date_change(tmp_path):
    """update_daily() should auto-rollover before applying the update."""
    clock = make_clock("2026-03-23")
    mgr = StateManager(data_dir=tmp_path, clock=clock)
    await mgr.initialize()
    await mgr.update_daily(off_today=True)

    # Advance the clock
    clock._start_sim = datetime(2026, 3, 24, 9, 0, tzinfo=TZ)

    await mgr.update_daily(declared_energy="high")
    daily = await mgr.get_daily()
    assert daily["date"] == "2026-03-24"
    assert daily["off_today"] is False  # rolled over, not carried
    assert daily["declared_energy"] == "high"  # today's update applied
