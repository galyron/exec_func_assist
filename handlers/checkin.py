"""C10 — Check-in Handler.

Handles both midday and evening check-ins, parameterised by CheckinType.
Sends an LLM-generated message with a Discord button View. Button clicks
and equivalent typed text ("done", "skip", "stuck") produce identical
state updates.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Awaitable, Callable

import discord

from config import Config
from context.assembler import AssembledContext
from handlers.base import BaseHandler, SendFn
from llm.client import LLMClient
from state.manager import StateManager
from utils.clock import Clock

log = logging.getLogger(__name__)


class CheckinType(str, Enum):
    MIDDAY = "midday"
    EVENING = "evening"


class CheckinHandler(BaseHandler):
    """C10 — Parameterised check-in for midday and evening slots.

    Args:
        config: Bot configuration.
        state_manager: For off_today check and interaction logging.
        clock: Clock instance.
        llm_client: Generates check-in and follow-up text.
        context_builder: Async callable returning a fresh AssembledContext.
    """

    def __init__(
        self,
        config: Config,
        state_manager: StateManager,
        clock: Clock,
        llm_client: LLMClient,
        context_builder: Callable[[], Awaitable[AssembledContext]],
    ) -> None:
        super().__init__(config, state_manager, clock)
        self._llm = llm_client
        self._build_context = context_builder

    # ── Scheduled entry point ─────────────────────────────────────────────────

    async def fire(self, checkin_type: CheckinType, send_fn: SendFn) -> None:
        """Send the check-in message. Called by the scheduler."""
        daily = await self._state.get_daily()
        if daily["off_today"]:
            return

        ctx = await self._build_context()
        now_str = self._clock.now().strftime("%H:%M")

        if checkin_type == CheckinType.MIDDAY:
            trigger = (
                f"It is {now_str}. Midday check-in for {self._config.user_name}. "
                "State what should be happening right now based on the task list. "
                "Name the single most important thing to execute before end of work day. "
                "Name the first physical action. No soft exits. Under 80 words."
            )
        else:
            trigger = (
                f"It is {now_str}. Evening check-in for {self._config.user_name}. "
                "Energy is lower — that is real. It does not mean the day is over. "
                "Suggest 1–2 couch-compatible tasks (tagged [couch], [low-energy], or [easy]). "
                "Name the first physical action for each. 15-minute commitment max. "
                "Make clear what it costs to skip even this: tomorrow starts heavier. "
                "Under 100 words. No empty comfort."
            )

        response = await self._llm.send(ctx, trigger)
        view = _CheckinView(handler=self)
        await send_fn(response, view=view)
        await self._log_bot(response)

    # ── Interactive entry points ──────────────────────────────────────────────

    async def handle_text_response(self, text: str, send_fn: SendFn) -> None:
        """Handle typed equivalents of button clicks."""
        t = text.lower().strip()
        if any(w in t for w in ("done", "all good", "good", "fine", "great", "ok", "okay")):
            await self._handle_good(send_fn)
        elif any(w in t for w in ("stuck", "struggling", "help", "difficult")):
            await self._handle_struggling(send_fn)
        elif "skip" in t:
            await self._handle_skip(send_fn)

    async def _handle_good(self, send_fn: SendFn) -> None:
        msg = f"Good. Keep moving, {self._config.user_name}."
        await send_fn(msg)
        await self._log_bot(msg)

    async def _handle_struggling(self, send_fn: SendFn) -> None:
        ctx = await self._build_context()
        trigger = (
            f"{self._config.user_name} says they're struggling. "
            "Name what is specifically blocking them. "
            "Give the single smallest physical action that breaks the freeze. "
            "Direct and brief — no comfort, no padding."
        )
        response = await self._llm.send(ctx, trigger)
        await send_fn(response)
        await self._log_bot(response)

    async def _handle_skip(self, send_fn: SendFn) -> None:
        msg = f"Skipped. That task is still waiting, {self._config.user_name}."
        await send_fn(msg)
        await self._log_bot(msg)


class _CheckinView(discord.ui.View):
    """Discord button row for check-in messages."""

    def __init__(self, handler: CheckinHandler) -> None:
        super().__init__(timeout=3600)  # buttons expire after 1 hour
        self._handler = handler

    @discord.ui.button(label="All good! 👍", style=discord.ButtonStyle.success)
    async def good_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.stop()
        await interaction.response.defer()
        await self._handler._handle_good(interaction.followup.send)

    @discord.ui.button(label="I'm struggling", style=discord.ButtonStyle.danger)
    async def struggling_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.stop()
        await interaction.response.defer()
        await self._handler._handle_struggling(interaction.followup.send)

    @discord.ui.button(label="Skip for now", style=discord.ButtonStyle.secondary)
    async def skip_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.stop()
        await interaction.response.defer()
        await self._handler._handle_skip(interaction.followup.send)
