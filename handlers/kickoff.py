"""C9 — Day Kick-off Handler.

Sends the structured day briefing at work_start: calendar summary,
free windows, and top 2–3 task suggestions with concrete first actions.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from config import Config
from context.assembler import AssembledContext
from handlers.base import BaseHandler, SendFn
from llm.client import LLMClient
from state.manager import StateManager
from utils.clock import Clock

log = logging.getLogger(__name__)


class KickoffHandler(BaseHandler):
    """C9 — Day briefing at work_start.

    Args:
        config: Bot configuration.
        state_manager: For off_today check and interaction logging.
        clock: Clock instance.
        llm_client: Generates the briefing text.
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

    async def fire(self, send_fn: SendFn) -> None:
        """Generate and send the day kick-off message. Called by the scheduler."""
        daily = await self._state.get_daily()
        if daily["off_today"]:
            return

        ctx = await self._build_context()
        now_str = self._clock.now().strftime("%A %Y-%m-%d %H:%M")
        trigger = (
            f"It is {now_str}. Generate the day kick-off message for {self._config.user_name}. "
            "Include: a one-line calendar summary, the key free windows, "
            "and the top 2–3 task suggestions with a specific first physical action for each. "
            "Keep it under 200 words."
        )
        response = await self._llm.send(ctx, trigger)
        await send_fn(response)
        await self._log_bot(response)
