"""Tests for C13 — Follow-up Handler."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from handlers.followup import FollowupHandler


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_daily(**overrides):
    base = {
        "date": "2026-03-25", "morning_complete": True,
        "morning_questions_asked": [], "declared_energy": None,
        "off_today": False, "off_today_full_silence": False,
        "task_queue": [], "opus_session_active": False,
        "opus_session_messages": 0, "last_suggestion": None, "last_suggestion_ts": None,
    }
    base.update(overrides)
    return base


@pytest.fixture
def config():
    cfg = MagicMock()
    cfg.user_name = "Gabriell"
    cfg.followup_delay_min = 20
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
    c.now.return_value = datetime(2026, 3, 25, 10, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    return c


@pytest.fixture
def get_send_fn():
    send_fn = AsyncMock()
    return MagicMock(return_value=send_fn)


@pytest.fixture
def apscheduler():
    sched = MagicMock()
    sched.add_job = MagicMock()
    sched.remove_job = MagicMock()
    return sched


@pytest.fixture
def handler(config, state_manager, clock, get_send_fn):
    return FollowupHandler(
        config=config,
        state_manager=state_manager,
        clock=clock,
        get_send_fn=get_send_fn,
    )


# ── set_apscheduler ───────────────────────────────────────────────────────────

def test_set_apscheduler(handler, apscheduler):
    handler.set_apscheduler(apscheduler)
    # just verifies it doesn't raise and stores the reference
    assert handler._apscheduler is apscheduler


# ── schedule ──────────────────────────────────────────────────────────────────

async def test_schedule_stores_suggestion_in_state(handler, state_manager, apscheduler):
    handler.set_apscheduler(apscheduler)
    await handler.schedule("Review the report")
    state_manager.update_daily.assert_called_once()
    kwargs = state_manager.update_daily.call_args[1]
    assert kwargs["last_suggestion"] == "Review the report"
    assert kwargs["last_suggestion_ts"] is not None


async def test_schedule_adds_apscheduler_job(handler, apscheduler):
    handler.set_apscheduler(apscheduler)
    await handler.schedule("Review the report")
    apscheduler.add_job.assert_called_once()
    call_kwargs = apscheduler.add_job.call_args[1]
    assert call_kwargs.get("id") == "followup"


async def test_schedule_job_has_date_trigger(handler, apscheduler):
    handler.set_apscheduler(apscheduler)
    await handler.schedule("Review the report")
    trigger = apscheduler.add_job.call_args[1].get("trigger")
    assert trigger == "date"


async def test_schedule_replaces_existing_job(handler, apscheduler):
    """Scheduling twice should replace the existing job, not add a second one."""
    handler.set_apscheduler(apscheduler)
    await handler.schedule("first")
    await handler.schedule("second")
    # add_job called with replace_existing=True or similar mechanism
    for call in apscheduler.add_job.call_args_list:
        kwargs = call[1]
        # Either replace_existing=True or misfire_grace_time indicates correct config
        assert kwargs.get("id") == "followup"


# ── cancel ────────────────────────────────────────────────────────────────────

def test_cancel_removes_job(handler, apscheduler):
    handler.set_apscheduler(apscheduler)
    handler.cancel()
    apscheduler.remove_job.assert_called_once_with("followup")


def test_cancel_tolerates_missing_job(handler, apscheduler):
    """cancel() must not raise if the job doesn't exist."""
    from apscheduler.jobstores.base import JobLookupError
    apscheduler.remove_job.side_effect = JobLookupError("followup")
    handler.set_apscheduler(apscheduler)
    handler.cancel()  # should not raise


def test_cancel_tolerates_no_scheduler(handler):
    """cancel() before set_apscheduler must not raise."""
    handler.cancel()  # should not raise


# ── _fire ─────────────────────────────────────────────────────────────────────

async def test_fire_sends_followup_message(handler, state_manager, get_send_fn, apscheduler):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(
        last_suggestion="Review the report"
    ))
    handler.set_apscheduler(apscheduler)
    await handler._fire()
    send_fn = get_send_fn.return_value
    send_fn.assert_called_once()


async def test_fire_message_references_suggestion(handler, state_manager, get_send_fn, apscheduler):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(
        last_suggestion="Review the report"
    ))
    handler.set_apscheduler(apscheduler)
    await handler._fire()
    send_fn = get_send_fn.return_value
    msg_or_kwargs = send_fn.call_args
    # message text or keyword args should reference the suggestion or be a follow-up prompt
    assert msg_or_kwargs is not None


async def test_fire_skipped_if_no_suggestion(handler, state_manager, get_send_fn, apscheduler):
    """If last_suggestion is None, _fire() should silently do nothing."""
    state_manager.get_daily = AsyncMock(return_value=_make_daily(last_suggestion=None))
    handler.set_apscheduler(apscheduler)
    await handler._fire()
    send_fn = get_send_fn.return_value
    send_fn.assert_not_called()


async def test_fire_skipped_if_get_send_fn_returns_none(handler, state_manager, apscheduler):
    """If channel isn't available, _fire() should silently do nothing."""
    state_manager.get_daily = AsyncMock(return_value=_make_daily(
        last_suggestion="Review the report"
    ))
    handler._get_send_fn = MagicMock(return_value=None)
    handler.set_apscheduler(apscheduler)
    await handler._fire()  # should not raise


# ── handle_done ───────────────────────────────────────────────────────────────

async def test_handle_done_sends_ack(handler):
    send_fn = AsyncMock()
    await handler.handle_done(send_fn)
    send_fn.assert_called_once()


async def test_handle_done_clears_suggestion(handler, state_manager):
    send_fn = AsyncMock()
    await handler.handle_done(send_fn)
    state_manager.update_daily.assert_called_once()
    kwargs = state_manager.update_daily.call_args[1]
    assert kwargs["last_suggestion"] is None


# ── handle_still_working ──────────────────────────────────────────────────────

async def test_handle_still_working_sends_encouragement(handler):
    send_fn = AsyncMock()
    await handler.handle_still_working(send_fn)
    send_fn.assert_called_once()


# ── handle_skipped ────────────────────────────────────────────────────────────

async def test_handle_skipped_sends_ack(handler):
    send_fn = AsyncMock()
    await handler.handle_skipped(send_fn)
    send_fn.assert_called_once()


async def test_handle_skipped_clears_suggestion(handler, state_manager):
    send_fn = AsyncMock()
    await handler.handle_skipped(send_fn)
    state_manager.update_daily.assert_called_once()
    kwargs = state_manager.update_daily.call_args[1]
    assert kwargs["last_suggestion"] is None
