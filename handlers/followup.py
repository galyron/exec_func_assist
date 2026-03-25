"""C13 — Follow-up Handler.

Schedules a one-shot APScheduler job that fires N minutes after a suggestion
is made. The user can respond via Discord buttons or typed commands.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

import discord
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import Config
from handlers.base import BaseHandler, SendFn
from state.manager import StateManager
from utils.clock import Clock

log = logging.getLogger(__name__)

_JOB_ID = "followup"


class FollowupHandler(BaseHandler):
    """C13 — Schedules and handles the 20-minute follow-up after a suggestion.

    Args:
        config: Bot configuration (user_name, followup_delay_min).
        state_manager: For reading/clearing last_suggestion.
        clock: Clock instance.
        get_send_fn: Callable that returns the current channel.send (or None).
    """

    def __init__(
        self,
        config: Config,
        state_manager: StateManager,
        clock: Clock,
        get_send_fn: Callable[[], Optional[SendFn]],
    ) -> None:
        super().__init__(config, state_manager, clock)
        self._get_send_fn = get_send_fn
        self._apscheduler: Optional[AsyncIOScheduler] = None

    def set_apscheduler(self, scheduler: AsyncIOScheduler) -> None:
        """Inject the APScheduler instance after creation (avoids circular dep)."""
        self._apscheduler = scheduler

    # ── Scheduling ────────────────────────────────────────────────────────────

    async def schedule(self, suggestion: str) -> None:
        """Store suggestion in state and schedule the follow-up job."""
        now = self._clock.now()
        await self._state.update_daily(
            last_suggestion=suggestion,
            last_suggestion_ts=now.isoformat(),
        )

        if self._apscheduler is None:
            log.warning("FollowupHandler.schedule() called before set_apscheduler()")
            return

        delay_min = getattr(self._config, "followup_delay_min", 20)
        from datetime import timedelta
        run_at = now + timedelta(minutes=delay_min)

        self._apscheduler.add_job(
            self._fire,
            trigger="date",
            run_date=run_at,
            id=_JOB_ID,
            replace_existing=True,
            misfire_grace_time=120,
        )
        log.info("Follow-up scheduled for %s", run_at.strftime("%H:%M"))

    def cancel(self) -> None:
        """Cancel the pending follow-up job, silently ignoring if absent."""
        if self._apscheduler is None:
            return
        try:
            self._apscheduler.remove_job(_JOB_ID)
            log.info("Follow-up cancelled.")
        except JobLookupError:
            pass

    # ── Follow-up fire ────────────────────────────────────────────────────────

    async def _fire(self) -> None:
        """Called by APScheduler. Sends the follow-up message with buttons."""
        daily = await self._state.get_daily()
        suggestion = daily.get("last_suggestion")
        if not suggestion:
            return

        send_fn = self._get_send_fn()
        if send_fn is None:
            log.warning("FollowupHandler._fire(): channel not available")
            return

        msg = (
            f"Hey {self._config.user_name} — checking in on that last suggestion. "
            "How did it go?"
        )
        view = _FollowupView(handler=self)
        await send_fn(msg, view=view)
        await self._log_bot(msg)

    # ── Response handlers (called by buttons or directly) ─────────────────────

    async def handle_done(self, send_fn: SendFn) -> None:
        """User completed the suggested task."""
        await self._state.update_daily(last_suggestion=None, last_suggestion_ts=None)
        msg = f"Excellent! Well done, {self._config.user_name}. 🎉"
        await send_fn(msg)
        await self._log_bot(msg)

    async def handle_still_working(self, send_fn: SendFn) -> None:
        """User is still working on it — just acknowledge."""
        msg = "Keep at it — you've got this. Let me know when you're done."
        await send_fn(msg)
        await self._log_bot(msg)

    async def handle_skipped(self, send_fn: SendFn) -> None:
        """User skipped the task — clear suggestion and acknowledge."""
        await self._state.update_daily(last_suggestion=None, last_suggestion_ts=None)
        msg = "No worries — it'll be there when you're ready."
        await send_fn(msg)
        await self._log_bot(msg)


# ── Discord View ──────────────────────────────────────────────────────────────

class _FollowupView(discord.ui.View):
    """Discord button row for follow-up messages."""

    def __init__(self, handler: FollowupHandler) -> None:
        super().__init__(timeout=3600)
        self._handler = handler

    @discord.ui.button(label="Done! ✅", style=discord.ButtonStyle.success)
    async def done_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.stop()
        await interaction.response.defer()
        await self._handler.handle_done(interaction.followup.send)

    @discord.ui.button(label="Still working on it", style=discord.ButtonStyle.primary)
    async def still_working_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.stop()
        await interaction.response.defer()
        await self._handler.handle_still_working(interaction.followup.send)

    @discord.ui.button(label="Skipped it", style=discord.ButtonStyle.secondary)
    async def skipped_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.stop()
        await interaction.response.defer()
        await self._handler.handle_skipped(interaction.followup.send)
