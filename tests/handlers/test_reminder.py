"""Tests for C15 — Reminder Handler."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from handlers.reminder import ReminderHandler, parse_reminder

TZ = ZoneInfo("Europe/Berlin")


# ── parse_reminder ───────────────────────────────────────────────────────────

def test_parse_remind_at_today():
    now = datetime(2026, 4, 8, 10, 0, tzinfo=TZ)
    result = parse_reminder("remind me at 14:30 about Hofstetter", now, TZ)
    assert result is not None
    text, fire_at = result
    assert text == "Hofstetter"
    assert fire_at.hour == 14
    assert fire_at.minute == 30
    assert fire_at.date() == now.date()


def test_parse_remind_at_colon_separator():
    now = datetime(2026, 4, 8, 10, 0, tzinfo=TZ)
    result = parse_reminder("remind me at 21:30: call Philipp about Veeva", now, TZ)
    assert result is not None
    text, fire_at = result
    assert "Philipp" in text
    assert fire_at.hour == 21
    assert fire_at.minute == 30


def test_parse_remind_at_dash_separator():
    now = datetime(2026, 4, 8, 10, 0, tzinfo=TZ)
    result = parse_reminder("reminder 14:30 — talk to Miriam about schneefang", now, TZ)
    assert result is not None
    text, fire_at = result
    assert "Miriam" in text
    assert fire_at.hour == 14


def test_parse_remind_at_dot_time():
    now = datetime(2026, 4, 8, 10, 0, tzinfo=TZ)
    result = parse_reminder("remind me at 14.30 about Hofstetter", now, TZ)
    assert result is not None
    text, fire_at = result
    assert fire_at.hour == 14
    assert fire_at.minute == 30


def test_parse_remind_past_time_rolls_to_tomorrow():
    now = datetime(2026, 4, 8, 15, 0, tzinfo=TZ)
    result = parse_reminder("remind me at 14:30 about something", now, TZ)
    assert result is not None
    _, fire_at = result
    assert fire_at.date() == datetime(2026, 4, 9).date()


def test_parse_remind_tomorrow():
    now = datetime(2026, 4, 8, 23, 0, tzinfo=TZ)
    result = parse_reminder("remind me tomorrow at 09:45: check kondensatpumpe", now, TZ)
    assert result is not None
    text, fire_at = result
    assert "kondensatpumpe" in text
    assert fire_at.date() == datetime(2026, 4, 9).date()
    assert fire_at.hour == 9
    assert fire_at.minute == 45


def test_parse_remind_on_day():
    now = datetime(2026, 4, 8, 10, 0, tzinfo=TZ)  # Wednesday
    result = parse_reminder("remind me on friday at 13:00: auto damage follow-up", now, TZ)
    assert result is not None
    text, fire_at = result
    assert "auto damage" in text
    assert fire_at.weekday() == 4  # Friday
    assert fire_at.hour == 13


def test_parse_remind_on_day_today_future():
    """If today is Wednesday and user says 'on wednesday at 18:00', fire today if time is future."""
    now = datetime(2026, 4, 8, 10, 0, tzinfo=TZ)  # Wednesday
    result = parse_reminder("remind me on wednesday at 18:00: do something", now, TZ)
    assert result is not None
    _, fire_at = result
    assert fire_at.date() == now.date()


def test_parse_remind_on_day_today_past():
    """If today is Wednesday and user says 'on wednesday at 08:00' but it's 10:00, fire next week."""
    now = datetime(2026, 4, 8, 10, 0, tzinfo=TZ)  # Wednesday
    result = parse_reminder("remind me on wednesday at 08:00: do something", now, TZ)
    assert result is not None
    _, fire_at = result
    assert fire_at.date() == datetime(2026, 4, 15).date()  # next Wednesday


def test_parse_not_a_reminder():
    now = datetime(2026, 4, 8, 10, 0, tzinfo=TZ)
    assert parse_reminder("what should I work on?", now, TZ) is None


def test_parse_remind_me_in_is_not_caught():
    """'remind me in 30 min' is a COMMIT timer, not a timed reminder."""
    now = datetime(2026, 4, 8, 10, 0, tzinfo=TZ)
    result = parse_reminder("remind me in 30 min to do something", now, TZ)
    # This should NOT match parse_reminder since there's no HH:MM time
    assert result is None


def test_parse_reminder_to_prefix():
    now = datetime(2026, 4, 8, 10, 0, tzinfo=TZ)
    result = parse_reminder("remind me at 14:30 to call the dentist", now, TZ)
    assert result is not None
    text, _ = result
    assert "dentist" in text


# ── ReminderHandler ──────────────────────────────────────────────────────────

def _make_daily(**overrides):
    base = {
        "date": "2026-04-08", "morning_complete": True,
        "morning_questions_asked": [], "declared_energy": None,
        "off_today": False, "off_today_full_silence": False,
        "task_queue": [], "opus_session_active": False,
        "opus_session_messages": 0, "last_suggestion": None, "last_suggestion_ts": None,
        "last_suggested_task_id": None, "commitment_minutes": None,
        "morning_retry_sent": False, "reminders": [], "reminder_counter": 0,
        "last_nudge_ts": None,
    }
    base.update(overrides)
    return base


@pytest.fixture
def config():
    cfg = MagicMock()
    cfg.user_name = "Gabriell"
    cfg.timezone = "Europe/Berlin"
    return cfg


@pytest.fixture
def state_manager():
    sm = MagicMock()
    sm.get_daily = AsyncMock(return_value=_make_daily())
    sm.update_daily = AsyncMock()
    sm.append_interaction = AsyncMock()
    return sm


@pytest.fixture
def clock():
    c = MagicMock()
    c.now.return_value = datetime(2026, 4, 8, 10, 0, tzinfo=TZ)
    return c


@pytest.fixture
def apscheduler():
    s = MagicMock()
    s.add_job = MagicMock()
    s.remove_job = MagicMock()
    return s


@pytest.fixture
def handler(config, state_manager, clock, apscheduler):
    h = ReminderHandler(
        config=config, state_manager=state_manager, clock=clock,
        get_send_fn=lambda: AsyncMock(),
    )
    h.set_apscheduler(apscheduler)
    return h


async def test_schedule_creates_job(handler, apscheduler, state_manager):
    fire_at = datetime(2026, 4, 8, 14, 30, tzinfo=TZ)
    job_id = await handler.schedule("Hofstetter call", fire_at)
    assert job_id == "reminder_1"
    apscheduler.add_job.assert_called_once()
    state_manager.update_daily.assert_called_once()
    kwargs = state_manager.update_daily.call_args[1]
    assert len(kwargs["reminders"]) == 1
    assert kwargs["reminders"][0]["text"] == "Hofstetter call"


async def test_schedule_increments_counter(handler, state_manager):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(reminder_counter=3))
    fire_at = datetime(2026, 4, 8, 14, 30, tzinfo=TZ)
    job_id = await handler.schedule("test", fire_at)
    assert job_id == "reminder_4"


async def test_cancel_removes_job(handler, apscheduler, state_manager):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(
        reminders=[{"id": "reminder_1", "text": "test", "fire_at": "2026-04-08T14:30:00", "created_at": "2026-04-08T10:00:00"}]
    ))
    await handler.cancel("reminder_1")
    apscheduler.remove_job.assert_called_once_with("reminder_1")


async def test_get_active_returns_reminders(handler, state_manager):
    reminders = [{"id": "r1", "text": "test", "fire_at": "2026-04-08T14:30:00", "created_at": "2026-04-08T10:00:00"}]
    state_manager.get_daily = AsyncMock(return_value=_make_daily(reminders=reminders))
    active = await handler.get_active()
    assert len(active) == 1
    assert active[0]["text"] == "test"
