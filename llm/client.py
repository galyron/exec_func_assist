"""C6 — LLM Client.

Wraps the Anthropic Python SDK. Selects model by session state,
injects the assembled context, tracks monthly spend, and enforces
the monthly cost cap.

Opus session lifecycle:
  - Activated when daily_state["opus_session_active"] is True
  - Message counter advances on each call
  - Reverts to Sonnet automatically after opus_session_max_messages
  - Deactivated by "off today" or explicit cancellation (Phase 2)

Standalone usage (end-to-end smoke test):
    docker compose exec bot python -m llm.client
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

import anthropic

from config import Config
from context.assembler import AssembledContext, Mode
from llm.prompts import get_system_prompt
from state.manager import StateManager
from state.models import Interaction

log = logging.getLogger(__name__)

_SONNET = "claude-sonnet-4-6"
_OPUS = "claude-opus-4-6"

# Approximate pricing in USD per token
_PRICING: dict[str, dict[str, float]] = {
    _SONNET: {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000},
    _OPUS:   {"input": 15.0 / 1_000_000, "output": 75.0 / 1_000_000},
}

_FALLBACK_MESSAGE = (
    "I'm having trouble thinking clearly right now — the monthly API budget "
    "has been reached. I'll be back to full capacity next month."
)


class LLMClient:
    """Anthropic API wrapper with model selection, spend tracking, and cost cap.

    Args:
        config: Bot configuration.
        state_manager: For spend tracking and Opus session state.
    """

    def __init__(self, config: Config, state_manager: StateManager) -> None:
        self._config = config
        self._state = state_manager
        self._client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    async def send(
        self,
        context: AssembledContext,
        user_message: str,
        *,
        system_override: Optional[str] = None,
    ) -> str:
        """Send a message to Claude and return the response text.

        Args:
            context: Assembled context (provides mode and formatted text payload).
            user_message: The trigger description or user message to append.
            system_override: Optional system prompt (e.g. for morning routine flow).

        Returns:
            Claude's response text, or a budget-exhausted fallback message.
        """
        state = await self._state.load_state()

        if not await self._check_spend_cap(state, context.now):
            log.warning("Monthly spend cap reached — returning fallback.")
            return _FALLBACK_MESSAGE

        model = self._select_model(state)
        system_base = system_override or get_system_prompt(context.mode)
        # Inject current state (tasks, calendar, mode) into the system prompt.
        # Add an explicit mode anchor so the LLM cannot be misled by conversation history.
        mode_anchor = (
            f"\n\nCURRENT STATE — OVERRIDES ALL PRIOR CONVERSATION:\n"
            f"Mode: {context.mode.value.upper()} | Time: {context.now.strftime('%A %Y-%m-%d %H:%M')} | "
            f"Energy: {context.energy}\n"
            f"Respond according to {context.mode.value.upper()} mode rules. "
            f"Ignore any tone, greetings, or mode references from prior messages in this conversation."
        )
        system = f"{system_base}\n\n{context.text}{mode_anchor}"
        messages = _build_messages(context.recent_interactions, user_message)

        try:
            response = await asyncio.to_thread(
                self._client.messages.create,
                model=model,
                max_tokens=1024,
                system=system,
                messages=messages,
            )
        except Exception as exc:
            log.error("LLM call failed: %s", exc)
            raise

        text = response.content[0].text if response.content else ""

        cost = (
            response.usage.input_tokens * _PRICING[model]["input"]
            + response.usage.output_tokens * _PRICING[model]["output"]
        )
        await self._record_spend(state, cost, context.now)
        await self._advance_opus_session(state)

        return text

    # ── Internal ──────────────────────────────────────────────────────────────

    def _select_model(self, state: dict) -> str:
        if state["daily"].get("opus_session_active"):
            return _OPUS
        return _SONNET

    async def _check_spend_cap(self, state: dict, now: datetime) -> bool:
        """Return True if we are under the monthly spend cap."""
        spend = state.get("monthly_spend", {})
        current_month = now.strftime("%Y-%m")
        if spend.get("month") != current_month:
            return True  # new month resets the counter
        return spend.get("usd", 0.0) < self._config.monthly_cost_limit_usd

    async def _record_spend(self, state: dict, cost_usd: float, now: datetime) -> None:
        current_month = now.strftime("%Y-%m")
        spend = state.get("monthly_spend", {})

        if spend.get("month") != current_month:
            state["monthly_spend"] = {"month": current_month, "usd": cost_usd}
        else:
            state["monthly_spend"]["usd"] = round(spend.get("usd", 0.0) + cost_usd, 6)

        await self._state.save_state(state)
        log.debug(
            "Recorded $%.6f — monthly total: $%.4f",
            cost_usd,
            state["monthly_spend"]["usd"],
        )

    async def _advance_opus_session(self, state: dict) -> None:
        if not state["daily"].get("opus_session_active"):
            return

        count = state["daily"].get("opus_session_messages", 0) + 1
        if count >= self._config.opus_session_max_messages:
            await self._state.update_daily(opus_session_active=False, opus_session_messages=0)
            log.info("Opus session ended after %d messages — reverted to Sonnet.", count)
        else:
            await self._state.update_daily(opus_session_messages=count)


# ── Message construction ──────────────────────────────────────────────────────

def _build_messages(interactions: list[Interaction], user_message: str) -> list[dict]:
    """Build alternating API message turns from interaction history + current trigger.

    The Anthropic API requires strict user/assistant alternation, starting and
    ending with "user". Consecutive same-role interactions are merged.
    """
    if not interactions:
        return [{"role": "user", "content": user_message}]

    turns: list[dict] = []
    for ix in interactions:
        role = "assistant" if ix["direction"] == "bot" else "user"
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n" + ix["content"]
        else:
            turns.append({"role": role, "content": ix["content"]})

    # API requires first message to be "user" — drop leading assistant turns
    while turns and turns[0]["role"] != "user":
        turns.pop(0)

    if not turns:
        return [{"role": "user", "content": user_message}]

    # Append current trigger as final "user" turn
    if turns[-1]["role"] == "user":
        # Last turn is already user — merge so we don't create consecutive user messages
        turns[-1]["content"] += f"\n\n{user_message}"
    else:
        turns.append({"role": "user", "content": user_message})

    return turns


# ── Standalone verification ───────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from pathlib import Path
    from config import load_config
    from connectors.calendar import CalendarConnector
    from connectors.joplin import JoplinConnector
    from context.assembler import ContextAssembler
    from state.manager import StateManager
    from utils.clock import RealClock

    _TOKEN_PATH = Path("secrets/google_token.json")

    async def _main() -> None:
        config = load_config()
        clock = RealClock(config.timezone)
        state_manager = StateManager(clock=clock)
        await state_manager.initialize()

        joplin = JoplinConnector(
            host=config.joplin_host,
            port=config.joplin_api_port,
            token=config.joplin_api_token,
        )
        calendar = CalendarConnector(
            token_path=_TOKEN_PATH,
            timezone=config.timezone,
            excluded_calendar_ids=config.excluded_calendar_ids,
            min_gap_min=config.min_gap_for_nudge_min,
        )

        tasks, events, interactions = await asyncio.gather(
            joplin.get_tasks(),
            calendar.get_events(),
            state_manager.get_recent_interactions(5),
        )

        assembler = ContextAssembler(config=config, state_manager=state_manager, clock=clock)
        ctx = await assembler.assemble(tasks=tasks, events=events, interactions=interactions)

        llm = LLMClient(config=config, state_manager=state_manager)

        print("=== Assembled Context ===")
        print(ctx.text)
        print(f"\nMode: {ctx.mode.value}  |  Energy: {ctx.energy}\n")

        trigger = f"It is {ctx.now.strftime('%H:%M')}. Generate the appropriate check-in message for {config.user_name}."
        print("=== LLM Response ===")
        response = await llm.send(ctx, trigger)
        print(response)

    asyncio.run(_main())
