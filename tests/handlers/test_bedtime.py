"""Tests for C11 — Bedtime + End-of-Day Handler."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from context.assembler import AssembledContext, Mode
from handlers.bedtime import BedtimeHandler


def _make_daily(*, off_today=False, off_today_full_silence=False):
    return {
        "date": "2026-03-25", "morning_complete": True,
        "morning_questions_asked": [], "declared_energy": None,
        "off_today": off_today, "off_today_full_silence": off_today_full_silence,
        "task_queue": [], "opus_session_active": False,
        "opus_session_messages": 0, "last_suggestion": None, "last_suggestion_ts": None,
    }


def _make_context():
    return AssembledContext(
        mode=Mode.RECOVERY, energy="low",
        now=datetime(2026, 3, 25, 22, 30, tzinfo=ZoneInfo("Europe/Berlin")),
        is_weekend=False, has_prior_history=True,
        tasks=[], events=[], free_windows=[], recent_interactions=[],
        daily_state=_make_daily(), text="=== context ===",
    )


def _make_interaction(content="did stuff"):
    return {
        "timestamp": "2026-03-25T10:00:00",
        "direction": "user",
        "content": content,
        "user_id": "default",
    }


@pytest.fixture
def config():
    cfg = MagicMock()
    cfg.user_name = "Gabriell"
    return cfg


@pytest.fixture
def state_manager():
    sm = MagicMock()
    sm.get_daily = AsyncMock(return_value=_make_daily())
    sm.get_today_interactions = AsyncMock(return_value=[_make_interaction()])
    sm.append_interaction = AsyncMock()
    return sm


@pytest.fixture
def clock():
    c = MagicMock()
    c.now.return_value = datetime(2026, 3, 25, 22, 30, tzinfo=ZoneInfo("Europe/Berlin"))
    return c


@pytest.fixture
def llm_client():
    llm = MagicMock()
    llm.send = AsyncMock(return_value="You did great today, Gabriell.")
    return llm


@pytest.fixture
def context_builder():
    return AsyncMock(return_value=_make_context())


@pytest.fixture
def handler(config, state_manager, clock, llm_client, context_builder):
    return BedtimeHandler(
        config=config,
        state_manager=state_manager,
        clock=clock,
        llm_client=llm_client,
        context_builder=context_builder,
    )


# ── fire_end_of_day ───────────────────────────────────────────────────────────

async def test_end_of_day_skipped_if_off_today(handler, state_manager):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(off_today=True))
    send_fn = AsyncMock()
    await handler.fire_end_of_day(send_fn)
    send_fn.assert_not_called()


async def test_end_of_day_skipped_if_no_interactions(handler, state_manager):
    state_manager.get_today_interactions = AsyncMock(return_value=[])
    send_fn = AsyncMock()
    await handler.fire_end_of_day(send_fn)
    send_fn.assert_not_called()


async def test_end_of_day_calls_llm_and_sends(handler, llm_client):
    send_fn = AsyncMock()
    await handler.fire_end_of_day(send_fn)
    llm_client.send.assert_called_once()
    send_fn.assert_called_once_with("You did great today, Gabriell.")


async def test_end_of_day_logs_interaction(handler, state_manager):
    await handler.fire_end_of_day(AsyncMock())
    state_manager.append_interaction.assert_called_once()


# ── fire_bedtime ──────────────────────────────────────────────────────────────

async def test_bedtime_fires_even_if_off_today(handler, state_manager):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(off_today=True))
    send_fn = AsyncMock()
    await handler.fire_bedtime(send_fn)
    send_fn.assert_called_once()


async def test_bedtime_skipped_if_full_silence(handler, state_manager):
    state_manager.get_daily = AsyncMock(
        return_value=_make_daily(off_today=True, off_today_full_silence=True)
    )
    send_fn = AsyncMock()
    await handler.fire_bedtime(send_fn)
    send_fn.assert_not_called()


async def test_bedtime_message_mentions_user(handler):
    send_fn = AsyncMock()
    await handler.fire_bedtime(send_fn)
    assert "Gabriell" in send_fn.call_args[0][0]


async def test_bedtime_logs_interaction(handler, state_manager):
    await handler.fire_bedtime(AsyncMock())
    state_manager.append_interaction.assert_called_once()
    assert state_manager.append_interaction.call_args[0][0]["direction"] == "bot"
