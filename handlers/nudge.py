"""C14-N — Periodic Nudge Handler.

Fires every 30 minutes during work hours. Checks whether the user is in a
calendar free window, respects the nudge cooldown, and sends an LLM-generated
nudge to pull the user back on task.

Also fires during GENERAL and RECOVERY modes with adjusted frequency.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

from config import Config
from context.assembler import AssembledContext
from handlers.base import BaseHandler, SendFn
from llm.client import LLMClient
from state.manager import StateManager
from utils.clock import Clock

log = logging.getLogger(__name__)


class NudgeHandler(BaseHandler):
    """Periodic nudge: checks free windows and cooldown, then sends an LLM nudge.

    Args:
        config: Bot configuration.
        state_manager: For cooldown tracking and off_today check.
        clock: Clock instance.
        llm_client: Generates nudge text.
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
        """Check conditions and send a nudge if appropriate."""
        daily = await self._state.get_daily()

        # Respect off_today
        if daily.get("off_today"):
            log.debug("Nudge suppressed: off_today is set.")
            return

        now = self._clock.now()

        # Skip weekends (nudge only fires if user initiates on weekends)
        if now.weekday() >= 5:
            log.debug("Nudge suppressed: weekend.")
            return

        # Respect cooldown
        last_nudge_str = daily.get("last_nudge_ts")
        if last_nudge_str:
            try:
                last_nudge = datetime.fromisoformat(last_nudge_str)
                elapsed = (now - last_nudge).total_seconds() / 60
                if elapsed < self._config.nudge_cooldown_min:
                    log.debug("Nudge suppressed: cooldown (%d min elapsed, %d min required).",
                              int(elapsed), self._config.nudge_cooldown_min)
                    return
            except (ValueError, TypeError):
                pass  # corrupt timestamp — ignore and proceed

        # Also respect cooldown against last bot interaction (any message counts)
        interactions = await self._state.get_recent_interactions(1)
        if interactions:
            last_ix = interactions[-1]
            if last_ix.get("direction") == "bot":
                try:
                    last_bot_ts = datetime.fromisoformat(last_ix["timestamp"])
                    elapsed = (now - last_bot_ts).total_seconds() / 60
                    if elapsed < self._config.nudge_cooldown_min:
                        log.debug("Nudge suppressed: recent bot message %d min ago.", int(elapsed))
                        return
                except (ValueError, TypeError):
                    pass

        # If there's an active commitment timer, don't nudge (the followup handler will)
        if daily.get("commitment_minutes") and daily.get("last_suggestion"):
            log.debug("Nudge suppressed: active commitment timer.")
            return

        # Build context and check free windows
        ctx = await self._build_context()

        # During work hours, only nudge if we're in a free window
        from context.assembler import Mode
        if ctx.mode == Mode.WORK:
            if not ctx.free_windows:
                log.debug("Nudge suppressed: no free windows during work hours.")
                return
            # Check if NOW is within a free window
            in_window = any(w.start <= now <= w.end for w in ctx.free_windows)
            if not in_window:
                log.debug("Nudge suppressed: not currently in a free window.")
                return

        # Generate and send the nudge
        trigger = self._build_trigger(ctx, now)
        response = await self._llm.send(ctx, trigger)
        await send_fn(response)
        await self._log_bot(response)

        # Record nudge timestamp
        await self._state.update_daily(last_nudge_ts=now.isoformat())
        log.info("Nudge sent at %s (mode=%s).", now.strftime("%H:%M"), ctx.mode.value)

    def _build_trigger(self, ctx: AssembledContext, now: datetime) -> str:
        from context.assembler import Mode

        now_str = now.strftime("%H:%M")
        name = self._config.user_name

        if ctx.mode == Mode.WORK:
            return (
                f"It is {now_str}. Proactive work-hours nudge for {name}. "
                f"This is a periodic check-in — {name} has not messaged recently. "
                "They may have drifted to distraction (web browsing, social media, phone). "
                "Name the single most important task from the context that should be worked on right now. "
                "Name the first physical action. Make the cost of continued inaction explicit. "
                "Under 60 words. No questions. No options. One directive."
            )
        elif ctx.mode == Mode.GENERAL:
            return (
                f"It is {now_str}. Proactive evening nudge for {name}. "
                "The work day is over but tasks remain. "
                "Name one task that can be done right now — quick admin, a reply, a note. "
                "First physical action. Cost of skipping it. Under 60 words."
            )
        else:  # RECOVERY
            return (
                f"It is {now_str}. {name} is likely on the couch. "
                "Break the idleness. Pick one couch-compatible task "
                "(tagged [couch], [low-energy], [easy]) from the context. "
                "Name the first physical action. 15-minute commitment max. "
                "Under 60 words. No comfort. No accommodation."
            )
