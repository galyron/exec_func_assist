"""Tests for C12 — On-Demand Handler."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from context.assembler import AssembledContext, Mode
from handlers.on_demand import Intent, OnDemandHandler, detect_intent


# ── detect_intent ─────────────────────────────────────────────────────────────

def test_intent_off_today():
    assert detect_intent("off today") == Intent.OFF_TODAY

def test_intent_off_today_case_insensitive():
    assert detect_intent("Off Today") == Intent.OFF_TODAY

def test_intent_full_silence():
    assert detect_intent("off today full silence") == Intent.OFF_TODAY
    # full_silence flag is parsed inside the handler, not a separate intent

def test_intent_finished_done():
    assert detect_intent("done") == Intent.FINISHED

def test_intent_finished_i_finished():
    assert detect_intent("I finished the report") == Intent.FINISHED

def test_intent_finished_done_with():
    assert detect_intent("done with the slides") == Intent.FINISHED

def test_intent_stuck():
    assert detect_intent("I'm stuck") == Intent.STUCK

def test_intent_stuck_struggling():
    assert detect_intent("I'm struggling with this") == Intent.STUCK

def test_intent_skip():
    assert detect_intent("skip") == Intent.SKIP

def test_intent_add_task_colon():
    assert detect_intent("add: buy milk") == Intent.ADD_TASK

def test_intent_use_opus():
    assert detect_intent("<USE_OPUS>") == Intent.USE_OPUS

def test_intent_use_opus_lowercase():
    assert detect_intent("<use_opus>") == Intent.USE_OPUS

def test_intent_general_fallback():
    assert detect_intent("how does the calendar look?") == Intent.GENERAL


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


def _make_context():
    return AssembledContext(
        mode=Mode.WORK, energy="medium",
        now=datetime(2026, 3, 25, 10, 0, tzinfo=ZoneInfo("Europe/Berlin")),
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
    sm.update_daily = AsyncMock()
    sm.append_interaction = AsyncMock()
    return sm


@pytest.fixture
def clock():
    c = MagicMock()
    c.now.return_value = datetime(2026, 3, 25, 10, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    return c


@pytest.fixture
def llm_client():
    llm = MagicMock()
    llm.send = AsyncMock(return_value="Here is a suggestion.")
    return llm


@pytest.fixture
def context_builder():
    return AsyncMock(return_value=_make_context())


@pytest.fixture
def followup_handler():
    fh = MagicMock()
    fh.schedule = AsyncMock()
    fh.cancel = MagicMock()
    return fh


@pytest.fixture
def handler(config, state_manager, clock, llm_client, context_builder, followup_handler):
    return OnDemandHandler(
        config=config,
        state_manager=state_manager,
        clock=clock,
        llm_client=llm_client,
        context_builder=context_builder,
        followup_handler=followup_handler,
    )


# ── off today ─────────────────────────────────────────────────────────────────

async def test_handle_off_today_sets_flag(handler, state_manager):
    await handler.handle("off today", AsyncMock())
    state_manager.update_daily.assert_called_once()
    kwargs = state_manager.update_daily.call_args[1]
    assert kwargs["off_today"] is True
    assert kwargs["off_today_full_silence"] is False


async def test_handle_off_today_full_silence(handler, state_manager):
    await handler.handle("off today full silence", AsyncMock())
    kwargs = state_manager.update_daily.call_args[1]
    assert kwargs["off_today_full_silence"] is True


async def test_handle_off_today_sends_ack(handler):
    send_fn = AsyncMock()
    await handler.handle("off today", send_fn)
    send_fn.assert_called_once()
    assert "quiet" in send_fn.call_args[0][0].lower() or "got it" in send_fn.call_args[0][0].lower()


# ── finished ──────────────────────────────────────────────────────────────────

async def test_handle_finished_sends_ack(handler):
    send_fn = AsyncMock()
    await handler.handle("I finished the report", send_fn)
    send_fn.assert_called_once()


async def test_handle_finished_cancels_followup(handler, followup_handler):
    await handler.handle("done", AsyncMock())
    followup_handler.cancel.assert_called_once()


async def test_handle_finished_logs_interaction(handler, state_manager):
    await handler.handle("done", AsyncMock())
    state_manager.append_interaction.assert_called()


# ── stuck ─────────────────────────────────────────────────────────────────────

async def test_handle_stuck_calls_llm(handler, llm_client):
    await handler.handle("I'm stuck", AsyncMock())
    llm_client.send.assert_called_once()


async def test_handle_stuck_schedules_followup(handler, followup_handler):
    await handler.handle("I'm stuck", AsyncMock())
    followup_handler.schedule.assert_called_once()


async def test_handle_stuck_sends_response(handler):
    send_fn = AsyncMock()
    await handler.handle("I'm stuck", send_fn)
    send_fn.assert_called_once_with("Here is a suggestion.")


# ── skip ─────────────────────────────────────────────────────────────────────

async def test_handle_skip_sends_message(handler):
    send_fn = AsyncMock()
    await handler.handle("skip", send_fn)
    send_fn.assert_called_once()


async def test_handle_skip_does_not_call_llm(handler, llm_client):
    await handler.handle("skip", AsyncMock())
    llm_client.send.assert_not_called()


# ── add task ─────────────────────────────────────────────────────────────────

async def test_handle_add_task_appends_to_queue(handler, state_manager):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(task_queue=[]))
    await handler.handle("add: buy milk", AsyncMock())
    state_manager.update_daily.assert_called_once()
    queue = state_manager.update_daily.call_args[1]["task_queue"]
    assert len(queue) == 1
    assert "buy milk" in queue[0]["title"]


async def test_handle_add_task_sends_confirmation(handler, state_manager):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(task_queue=[]))
    send_fn = AsyncMock()
    await handler.handle("add: buy milk", send_fn)
    send_fn.assert_called_once()
    assert "buy milk" in send_fn.call_args[0][0].lower()


# ── use opus ─────────────────────────────────────────────────────────────────

async def test_handle_use_opus_activates_session(handler, state_manager):
    await handler.handle("<USE_OPUS>", AsyncMock())
    state_manager.update_daily.assert_called_once_with(
        opus_session_active=True, opus_session_messages=0
    )


async def test_handle_use_opus_sends_confirmation(handler):
    send_fn = AsyncMock()
    await handler.handle("<USE_OPUS>", send_fn)
    send_fn.assert_called_once()
    assert "opus" in send_fn.call_args[0][0].lower()


# ── general ──────────────────────────────────────────────────────────────────

async def test_handle_general_calls_llm(handler, llm_client):
    await handler.handle("what should I work on?", AsyncMock())
    llm_client.send.assert_called_once()


async def test_handle_general_sends_llm_response(handler):
    send_fn = AsyncMock()
    await handler.handle("what should I work on?", send_fn)
    send_fn.assert_called_once_with("Here is a suggestion.")
