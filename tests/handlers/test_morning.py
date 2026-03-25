"""Tests for C8 — Morning Routine Handler."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from handlers.morning import MorningRoutineHandler, _parse_energy


# ── _parse_energy ─────────────────────────────────────────────────────────────

def test_parse_energy_low():
    assert _parse_energy("pretty tired today") == "low"

def test_parse_energy_high():
    assert _parse_energy("feeling great and energetic!") == "high"

def test_parse_energy_medium_default():
    assert _parse_energy("okay I guess") == "medium"

def test_parse_energy_medium_explicit():
    assert _parse_energy("medium, fine") == "medium"


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_daily(
    *,
    off_today=False,
    morning_complete=False,
    morning_questions_asked=None,
    declared_energy=None,
):
    return {
        "date": "2026-03-25",
        "morning_complete": morning_complete,
        "morning_questions_asked": morning_questions_asked or [],
        "declared_energy": declared_energy,
        "off_today": off_today,
        "off_today_full_silence": False,
        "task_queue": [],
        "opus_session_active": False,
        "opus_session_messages": 0,
        "last_suggestion": None,
        "last_suggestion_ts": None,
    }


def _make_context():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from context.assembler import AssembledContext, Mode
    return AssembledContext(
        mode=Mode.MORNING, energy="medium",
        now=datetime(2026, 3, 25, 7, 30, tzinfo=ZoneInfo("Europe/Berlin")),
        is_weekend=False, has_prior_history=False,
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
    sm.get_recent_interactions = AsyncMock(return_value=[])
    return sm


@pytest.fixture
def clock():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    c = MagicMock()
    c.now.return_value = datetime(2026, 3, 25, 7, 30, tzinfo=ZoneInfo("Europe/Berlin"))
    return c


@pytest.fixture
def llm_client():
    llm = MagicMock()
    llm.send = AsyncMock(return_value="Great, let's make it a productive day!")
    return llm


@pytest.fixture
def context_builder():
    return AsyncMock(return_value=_make_context())


@pytest.fixture
def handler(config, state_manager, clock, llm_client, context_builder):
    return MorningRoutineHandler(
        config=config,
        state_manager=state_manager,
        clock=clock,
        llm_client=llm_client,
        context_builder=context_builder,
    )


# ── fire ─────────────────────────────────────────────────────────────────────

async def test_fire_skipped_if_off_today(handler, state_manager):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(off_today=True))
    send_fn = AsyncMock()
    await handler.fire(send_fn)
    send_fn.assert_not_called()


async def test_fire_skipped_if_already_complete(handler, state_manager):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(morning_complete=True))
    send_fn = AsyncMock()
    await handler.fire(send_fn)
    send_fn.assert_not_called()


async def test_fire_skipped_if_already_in_progress(handler, state_manager):
    state_manager.get_daily = AsyncMock(
        return_value=_make_daily(morning_questions_asked=["energy"])
    )
    send_fn = AsyncMock()
    await handler.fire(send_fn)
    send_fn.assert_not_called()


async def test_fire_sends_first_question(handler, state_manager):
    send_fn = AsyncMock()
    await handler.fire(send_fn)
    send_fn.assert_called_once()
    assert "Good morning" in send_fn.call_args[0][0]


async def test_fire_updates_questions_asked(handler, state_manager):
    send_fn = AsyncMock()
    await handler.fire(send_fn)
    state_manager.update_daily.assert_called_once_with(morning_questions_asked=["energy"])


# ── fire_retry ────────────────────────────────────────────────────────────────

async def test_retry_skipped_if_complete(handler, state_manager):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(morning_complete=True))
    send_fn = AsyncMock()
    await handler.fire_retry(send_fn)
    send_fn.assert_not_called()


async def test_retry_skipped_if_off_today(handler, state_manager):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(off_today=True))
    send_fn = AsyncMock()
    await handler.fire_retry(send_fn)
    send_fn.assert_not_called()


async def test_retry_sends_if_not_answered(handler, state_manager):
    state_manager.get_daily = AsyncMock(return_value=_make_daily())
    send_fn = AsyncMock()
    await handler.fire_retry(send_fn)
    send_fn.assert_called_once()


# ── handle_response ───────────────────────────────────────────────────────────

async def test_handle_response_returns_false_if_not_in_progress(handler, state_manager):
    # No questions asked yet → not in morning routine conversation
    result = await handler.handle_response("hello", AsyncMock())
    assert result is False


async def test_handle_response_energy_asks_goal_question(handler, state_manager):
    state_manager.get_daily = AsyncMock(
        return_value=_make_daily(morning_questions_asked=["energy"])
    )
    send_fn = AsyncMock()
    result = await handler.handle_response("I'm feeling okay", send_fn)
    assert result is False
    assert "today" in send_fn.call_args[0][0].lower()


async def test_handle_response_energy_updates_declared_energy(handler, state_manager):
    state_manager.get_daily = AsyncMock(
        return_value=_make_daily(morning_questions_asked=["energy"])
    )
    await handler.handle_response("I'm exhausted", AsyncMock())
    state_manager.update_daily.assert_called_once()
    call_kwargs = state_manager.update_daily.call_args[1]
    assert call_kwargs["declared_energy"] == "low"
    assert "goal" in call_kwargs["morning_questions_asked"]


async def test_handle_response_goal_asks_blockers(handler, state_manager):
    state_manager.get_daily = AsyncMock(
        return_value=_make_daily(morning_questions_asked=["energy", "goal"])
    )
    send_fn = AsyncMock()
    result = await handler.handle_response("Finish the report", send_fn)
    assert result is False
    assert "way" in send_fn.call_args[0][0].lower() or "mind" in send_fn.call_args[0][0].lower()


async def test_handle_response_blockers_marks_complete(handler, state_manager):
    state_manager.get_daily = AsyncMock(
        return_value=_make_daily(morning_questions_asked=["energy", "goal", "blockers"])
    )
    send_fn = AsyncMock()
    result = await handler.handle_response("Nothing blocking me", send_fn)
    assert result is True
    state_manager.update_daily.assert_called_with(morning_complete=True)


async def test_handle_response_blockers_calls_llm(handler, state_manager, llm_client):
    state_manager.get_daily = AsyncMock(
        return_value=_make_daily(morning_questions_asked=["energy", "goal", "blockers"])
    )
    await handler.handle_response("Nothing blocking me", AsyncMock())
    llm_client.send.assert_called_once()


# ── is_active ─────────────────────────────────────────────────────────────────

async def test_is_active_false_when_no_questions_asked(handler, state_manager):
    assert await handler.is_active() is False


async def test_is_active_true_when_in_progress(handler, state_manager):
    state_manager.get_daily = AsyncMock(
        return_value=_make_daily(morning_questions_asked=["energy"])
    )
    assert await handler.is_active() is True


async def test_is_active_false_when_complete(handler, state_manager):
    state_manager.get_daily = AsyncMock(
        return_value=_make_daily(morning_questions_asked=["energy", "goal", "blockers"], morning_complete=True)
    )
    assert await handler.is_active() is False
