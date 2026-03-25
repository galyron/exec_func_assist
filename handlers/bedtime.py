"""C11 — Bedtime Reminder + End-of-Day Review Handler.

End-of-day review fires at end_of_day_review time; bedtime reminder fires
at bedtime. Bedtime is the only message exempt from 'off today' suppression
(unless 'full silence' was requested).
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


class BedtimeHandler(BaseHandler):
    """C11 — End-of-day review and bedtime reminder.

    Args:
        config: Bot configuration.
        state_manager: For off_today checks and interaction log.
        clock: Clock instance.
        llm_client: Generates the end-of-day review.
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

    async def fire_end_of_day(self, send_fn: SendFn) -> None:
        """LLM-generated micro-review from today's interactions.

        Skipped if off_today or if there are no interactions to review.
        """
        daily = await self._state.get_daily()
        if daily["off_today"]:
            return

        today_interactions = await self._state.get_today_interactions()
        if not today_interactions:
            return

        ctx = await self._build_context()
        now_str = self._clock.now().strftime("%A %Y-%m-%d %H:%M")
        trigger = (
            f"It is {now_str}. End-of-day review for {self._config.user_name}. "
            f"Today had {len(today_interactions)} recorded exchanges. "
            "2–3 sentences: what got done, what didn't, and the one thing that must "
            "happen first tomorrow. Be direct — no comfort, no softening. "
            "Unfinished work is a debt. Name it plainly."
        )
        response = await self._llm.send(ctx, trigger)
        await send_fn(response)
        await self._log_bot(response)

    async def fire_bedtime(self, send_fn: SendFn) -> None:
        """Bedtime reminder. Fires even on 'off today' unless 'full silence' was set."""
        daily = await self._state.get_daily()
        if daily.get("off_today_full_silence"):
            return

        msg = (
            f"Rest now, {self._config.user_name}. "
            "Tomorrow starts where today left off — make it count."
        )
        await send_fn(msg)
        await self._log_bot(msg)
