"""Tests for C6 — LLM Client."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from context.assembler import AssembledContext, Mode
from llm.client import LLMClient, _FALLBACK_MESSAGE, _OPUS, _SONNET

TZ = ZoneInfo("Europe/Berlin")


def _make_context(mode: Mode = Mode.WORK) -> AssembledContext:
    return AssembledContext(
        mode=mode,
        energy="medium",
        now=datetime(2026, 3, 25, 10, 0, tzinfo=TZ),
        is_weekend=False,
        has_prior_history=True,
        tasks=[],
        events=[],
        free_windows=[],
        recent_interactions=[],
        daily_state={
            "date": "2026-03-25",
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
        },
        text="=== EVA Context ===",
    )


def _make_state(opus_active: bool = False, monthly_usd: float = 0.0) -> dict:
    return {
        "user_id": "default",
        "first_run_completed": True,
        "daily": {
            "date": "2026-03-25",
            "morning_complete": False,
            "morning_questions_asked": [],
            "declared_energy": None,
            "off_today": False,
            "off_today_full_silence": False,
            "task_queue": [],
            "opus_session_active": opus_active,
            "opus_session_messages": 0,
            "last_suggestion": None,
            "last_suggestion_ts": None,
        },
        "previous_daily": None,
        "monthly_spend": {"month": "2026-03", "usd": monthly_usd},
    }


def _make_api_response(text: str = "Here is your response.") -> MagicMock:
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    resp.usage = MagicMock(input_tokens=100, output_tokens=50)
    return resp


@pytest.fixture
def config():
    cfg = MagicMock()
    cfg.anthropic_api_key = "test-key"
    cfg.monthly_cost_limit_usd = 10.0
    cfg.opus_session_max_messages = 10
    return cfg


@pytest.fixture
def state_manager():
    sm = MagicMock()
    sm.load_state = AsyncMock(return_value=_make_state())
    sm.save_state = AsyncMock()
    sm.update_daily = AsyncMock()
    return sm


@pytest.fixture
def client(config, state_manager):
    with patch("anthropic.Anthropic"):
        return LLMClient(config=config, state_manager=state_manager)


# ── Model selection ───────────────────────────────────────────────────────────

def test_uses_sonnet_by_default(client):
    assert client._select_model(_make_state(opus_active=False)) == _SONNET


def test_uses_opus_when_session_active(client):
    assert client._select_model(_make_state(opus_active=True)) == _OPUS


# ── Spend cap ────────────────────────────────────────────────────────────────

async def test_under_cap_returns_true(client):
    ctx = _make_context()
    assert await client._check_spend_cap(_make_state(monthly_usd=5.0), ctx.now) is True


async def test_at_cap_returns_false(client):
    ctx = _make_context()
    assert await client._check_spend_cap(_make_state(monthly_usd=10.0), ctx.now) is False


async def test_over_cap_returns_fallback(client, state_manager):
    state_manager.load_state = AsyncMock(return_value=_make_state(monthly_usd=10.0))
    result = await client.send(_make_context(), "What should I do?")
    assert result == _FALLBACK_MESSAGE


# ── Spend tracking ────────────────────────────────────────────────────────────

async def test_spend_recorded_after_call(client, state_manager):
    state_manager.load_state = AsyncMock(return_value=_make_state())
    with patch("asyncio.to_thread", new=AsyncMock(return_value=_make_api_response())):
        await client.send(_make_context(), "Hello")

    state_manager.save_state.assert_called()
    saved = state_manager.save_state.call_args[0][0]
    assert saved["monthly_spend"]["usd"] > 0


async def test_spend_accumulates(client, state_manager):
    state_manager.load_state = AsyncMock(return_value=_make_state(monthly_usd=1.0))
    with patch("asyncio.to_thread", new=AsyncMock(return_value=_make_api_response())):
        await client.send(_make_context(), "Hello")

    saved = state_manager.save_state.call_args[0][0]
    assert saved["monthly_spend"]["usd"] > 1.0


# ── Response ─────────────────────────────────────────────────────────────────

async def test_response_text_returned(client, state_manager):
    with patch("asyncio.to_thread", new=AsyncMock(return_value=_make_api_response("Great plan!"))):
        result = await client.send(_make_context(), "Hello")
    assert result == "Great plan!"


# ── Opus session lifecycle ────────────────────────────────────────────────────

async def test_opus_message_counter_increments(client, state_manager):
    state = _make_state(opus_active=True)
    state["daily"]["opus_session_messages"] = 3
    state_manager.load_state = AsyncMock(return_value=state)

    with patch("asyncio.to_thread", new=AsyncMock(return_value=_make_api_response())):
        await client.send(_make_context(), "Hello")

    state_manager.update_daily.assert_called_once_with(opus_session_messages=4)


async def test_opus_session_reverts_at_max(client, state_manager, config):
    config.opus_session_max_messages = 5
    state = _make_state(opus_active=True)
    state["daily"]["opus_session_messages"] = 4  # next message hits the limit
    state_manager.load_state = AsyncMock(return_value=state)

    with patch("asyncio.to_thread", new=AsyncMock(return_value=_make_api_response())):
        await client.send(_make_context(), "Hello")

    state_manager.update_daily.assert_called_once_with(
        opus_session_active=False, opus_session_messages=0
    )
