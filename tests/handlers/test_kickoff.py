"""Tests for C9 — Day Kick-off Handler."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from context.assembler import AssembledContext, Mode
from handlers.kickoff import KickoffHandler


def _make_daily(*, off_today=False):
    return {
        "date": "2026-03-25", "morning_complete": True,
        "morning_questions_asked": [], "declared_energy": None,
        "off_today": off_today, "off_today_full_silence": False,
        "task_queue": [], "opus_session_active": False,
        "opus_session_messages": 0, "last_suggestion": None, "last_suggestion_ts": None,
    }


def _make_context():
    return AssembledContext(
        mode=Mode.WORK, energy="medium",
        now=datetime(2026, 3, 25, 9, 15, tzinfo=ZoneInfo("Europe/Berlin")),
        is_weekend=False, has_prior_history=True,
        tasks=[], events=[], free_windows=[], recent_interactions=[],
        daily_state=_make_daily(), text="=== context ===",
    )


@pytest.fixture
def config():
    cfg = MagicMock()
    cfg.user_name = "Gabriell"
    return cfg


@pytest.fixture
def state_manager():
    sm = MagicMock()
    sm.get_daily = AsyncMock(return_value=_make_daily())
    sm.append_interaction = AsyncMock()
    return sm


@pytest.fixture
def clock():
    c = MagicMock()
    c.now.return_value = datetime(2026, 3, 25, 9, 15, tzinfo=ZoneInfo("Europe/Berlin"))
    return c


@pytest.fixture
def llm_client():
    llm = MagicMock()
    llm.send = AsyncMock(return_value="Here's your day briefing!")
    return llm


@pytest.fixture
def context_builder():
    return AsyncMock(return_value=_make_context())


@pytest.fixture
def handler(config, state_manager, clock, llm_client, context_builder):
    return KickoffHandler(
        config=config,
        state_manager=state_manager,
        clock=clock,
        llm_client=llm_client,
        context_builder=context_builder,
    )


async def test_fire_skipped_if_off_today(handler, state_manager):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(off_today=True))
    send_fn = AsyncMock()
    await handler.fire(send_fn)
    send_fn.assert_not_called()


async def test_fire_calls_llm(handler, llm_client):
    await handler.fire(AsyncMock())
    llm_client.send.assert_called_once()


async def test_fire_sends_response(handler):
    send_fn = AsyncMock()
    await handler.fire(send_fn)
    send_fn.assert_called_once_with("Here's your day briefing!")


async def test_fire_logs_interaction(handler, state_manager):
    await handler.fire(AsyncMock())
    state_manager.append_interaction.assert_called_once()
    call_args = state_manager.append_interaction.call_args[0][0]
    assert call_args["direction"] == "bot"
    assert "briefing" in call_args["content"]
