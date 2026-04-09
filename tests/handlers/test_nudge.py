"""Tests for C14-N — Nudge Handler."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from connectors.models import FreeWindow
from context.assembler import AssembledContext, Mode
from handlers.nudge import NudgeHandler

TZ = ZoneInfo("Europe/Berlin")


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


def _make_context(mode=Mode.WORK, free_windows=None, now=None):
    if now is None:
        now = datetime(2026, 4, 8, 11, 0, tzinfo=TZ)
    return AssembledContext(
        mode=mode, energy="medium", now=now,
        is_weekend=False, has_prior_history=True,
        tasks=[], events=[], free_windows=free_windows or [],
        recent_interactions=[], daily_state=_make_daily(), text="=== context ===",
    )


@pytest.fixture
def config():
    cfg = MagicMock()
    cfg.user_name = "Gabriell"
    cfg.timezone = "Europe/Berlin"
    cfg.nudge_cooldown_min = 45
    return cfg


@pytest.fixture
def state_manager():
    sm = MagicMock()
    sm.get_daily = AsyncMock(return_value=_make_daily())
    sm.update_daily = AsyncMock()
    sm.append_interaction = AsyncMock()
    sm.get_recent_interactions = AsyncMock(return_value=[])
    return sm


@pytest.fixture
def clock():
    c = MagicMock()
    c.now.return_value = datetime(2026, 4, 8, 11, 0, tzinfo=TZ)
    return c


@pytest.fixture
def llm_client():
    llm = MagicMock()
    llm.send = AsyncMock(return_value="Get back to the deck. Open the file now.")
    return llm


@pytest.fixture
def context_builder():
    now = datetime(2026, 4, 8, 11, 0, tzinfo=TZ)
    free_windows = [FreeWindow(
        start=datetime(2026, 4, 8, 10, 0, tzinfo=TZ),
        end=datetime(2026, 4, 8, 12, 0, tzinfo=TZ),
    )]
    return AsyncMock(return_value=_make_context(free_windows=free_windows, now=now))


@pytest.fixture
def handler(config, state_manager, clock, llm_client, context_builder):
    return NudgeHandler(
        config=config, state_manager=state_manager, clock=clock,
        llm_client=llm_client, context_builder=context_builder,
    )


async def test_nudge_fires_in_free_window(handler, llm_client, state_manager):
    send_fn = AsyncMock()
    await handler.fire(send_fn)
    llm_client.send.assert_called_once()
    send_fn.assert_called_once()
    state_manager.update_daily.assert_called()
    # Should record last_nudge_ts
    last_call_kwargs = state_manager.update_daily.call_args[1]
    assert "last_nudge_ts" in last_call_kwargs


async def test_nudge_suppressed_off_today(handler, state_manager, llm_client):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(off_today=True))
    send_fn = AsyncMock()
    await handler.fire(send_fn)
    llm_client.send.assert_not_called()
    send_fn.assert_not_called()


async def test_nudge_suppressed_by_cooldown(handler, state_manager, llm_client, clock):
    """If last nudge was 20 min ago and cooldown is 45 min, suppress."""
    last_nudge = datetime(2026, 4, 8, 10, 40, tzinfo=TZ)
    state_manager.get_daily = AsyncMock(return_value=_make_daily(
        last_nudge_ts=last_nudge.isoformat()
    ))
    send_fn = AsyncMock()
    await handler.fire(send_fn)
    llm_client.send.assert_not_called()


async def test_nudge_fires_after_cooldown(handler, state_manager, llm_client, clock):
    """If last nudge was 50 min ago and cooldown is 45 min, fire."""
    last_nudge = datetime(2026, 4, 8, 10, 10, tzinfo=TZ)
    state_manager.get_daily = AsyncMock(return_value=_make_daily(
        last_nudge_ts=last_nudge.isoformat()
    ))
    send_fn = AsyncMock()
    await handler.fire(send_fn)
    llm_client.send.assert_called_once()


async def test_nudge_suppressed_on_weekend(handler, state_manager, llm_client, clock):
    """Nudge should not fire on weekends."""
    clock.now.return_value = datetime(2026, 4, 11, 11, 0, tzinfo=TZ)  # Saturday
    send_fn = AsyncMock()
    await handler.fire(send_fn)
    llm_client.send.assert_not_called()


async def test_nudge_suppressed_no_free_window(handler, state_manager, llm_client, context_builder):
    """During work mode, suppress nudge if not in a free window."""
    context_builder.return_value = _make_context(free_windows=[])
    send_fn = AsyncMock()
    await handler.fire(send_fn)
    llm_client.send.assert_not_called()


async def test_nudge_suppressed_active_timer(handler, state_manager, llm_client):
    """If user has an active commitment timer, the followup handler handles it."""
    state_manager.get_daily = AsyncMock(return_value=_make_daily(
        commitment_minutes=20, last_suggestion="some task"
    ))
    send_fn = AsyncMock()
    await handler.fire(send_fn)
    llm_client.send.assert_not_called()


async def test_nudge_suppressed_by_recent_bot_message(handler, state_manager, llm_client):
    """If bot sent a message 10 min ago, don't nudge (within cooldown)."""
    state_manager.get_recent_interactions = AsyncMock(return_value=[{
        "direction": "bot",
        "timestamp": datetime(2026, 4, 8, 10, 50, tzinfo=TZ).isoformat(),
        "content": "some message",
    }])
    send_fn = AsyncMock()
    await handler.fire(send_fn)
    llm_client.send.assert_not_called()


async def test_nudge_general_mode_no_free_window_check(handler, state_manager, llm_client, context_builder, clock):
    """In GENERAL mode, nudge fires without checking free windows."""
    clock.now.return_value = datetime(2026, 4, 8, 17, 0, tzinfo=TZ)
    context_builder.return_value = _make_context(mode=Mode.GENERAL, free_windows=[])
    send_fn = AsyncMock()
    await handler.fire(send_fn)
    llm_client.send.assert_called_once()
