"""Tests for C10 — Check-in Handler."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from context.assembler import AssembledContext, Mode
from handlers.checkin import CheckinHandler, CheckinType


def _make_daily(*, off_today=False):
    return {
        "date": "2026-03-25", "morning_complete": True,
        "morning_questions_asked": [], "declared_energy": None,
        "off_today": off_today, "off_today_full_silence": False,
        "task_queue": [], "opus_session_active": False,
        "opus_session_messages": 0, "last_suggestion": None, "last_suggestion_ts": None,
    }


def _make_context(mode=Mode.WORK):
    return AssembledContext(
        mode=mode, energy="medium",
        now=datetime(2026, 3, 25, 13, 0, tzinfo=ZoneInfo("Europe/Berlin")),
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
    c.now.return_value = datetime(2026, 3, 25, 13, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    return c


@pytest.fixture
def llm_client():
    llm = MagicMock()
    llm.send = AsyncMock(return_value="How's the afternoon going?")
    return llm


@pytest.fixture
def context_builder():
    return AsyncMock(return_value=_make_context())


@pytest.fixture
def handler(config, state_manager, clock, llm_client, context_builder):
    return CheckinHandler(
        config=config,
        state_manager=state_manager,
        clock=clock,
        llm_client=llm_client,
        context_builder=context_builder,
    )


# ── off_today guard ───────────────────────────────────────────────────────────

async def test_midday_skipped_if_off_today(handler, state_manager):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(off_today=True))
    send_fn = AsyncMock()
    await handler.fire(CheckinType.MIDDAY, send_fn)
    send_fn.assert_not_called()


async def test_evening_skipped_if_off_today(handler, state_manager):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(off_today=True))
    send_fn = AsyncMock()
    await handler.fire(CheckinType.EVENING, send_fn)
    send_fn.assert_not_called()


# ── LLM call ─────────────────────────────────────────────────────────────────

async def test_midday_calls_llm(handler, llm_client):
    await handler.fire(CheckinType.MIDDAY, AsyncMock())
    llm_client.send.assert_called_once()
    trigger = llm_client.send.call_args[0][1]
    assert "midday" in trigger.lower()


async def test_evening_calls_llm(handler, llm_client):
    await handler.fire(CheckinType.EVENING, AsyncMock())
    llm_client.send.assert_called_once()
    trigger = llm_client.send.call_args[0][1]
    assert "evening" in trigger.lower() or "recovery" in trigger.lower()


async def test_evening_trigger_mentions_couch_tasks(handler, llm_client):
    await handler.fire(CheckinType.EVENING, AsyncMock())
    trigger = llm_client.send.call_args[0][1]
    assert "couch" in trigger.lower()


# ── text response handling ────────────────────────────────────────────────────

async def test_handle_text_response_good(handler):
    send_fn = AsyncMock()
    await handler.handle_text_response("all good!", send_fn)
    send_fn.assert_called_once()
    assert "good" in send_fn.call_args[0][0].lower() or "moving" in send_fn.call_args[0][0].lower()


async def test_handle_text_response_skip(handler):
    send_fn = AsyncMock()
    await handler.handle_text_response("skip", send_fn)
    send_fn.assert_called_once()


async def test_handle_text_response_struggling_calls_llm(handler, llm_client):
    send_fn = AsyncMock()
    await handler.handle_text_response("I'm stuck", send_fn)
    llm_client.send.assert_called_once()
