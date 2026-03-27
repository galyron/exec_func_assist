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

    async def schedule(self, suggestion: str, minutes: Optional[int] = None) -> None:
        """Store suggestion in state and schedule the follow-up job.

        Args:
            suggestion: Task description shown in the follow-up message.
            minutes: Timer duration. Falls back to config.followup_delay_min if None.
        """
        now = self._clock.now()
        delay_min = minutes if minutes is not None else getattr(self._config, "followup_delay_min", 20)

        await self._state.update_daily(
            last_suggestion=suggestion,
            last_suggestion_ts=now.isoformat(),
            commitment_minutes=delay_min,
        )

        if self._apscheduler is None:
            log.warning("FollowupHandler.schedule() called before set_apscheduler()")
            return

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
        log.info("Follow-up scheduled for %s (%d min)", run_at.strftime("%H:%M"), delay_min)

    async def handle_timer_set(self, suggestion: str, minutes: int, send_fn: SendFn) -> None:
        """Called when user picks a duration from the timer picker view."""
        await self.schedule(suggestion, minutes=minutes)
        from datetime import timedelta
        at_time = (self._clock.now() + timedelta(minutes=minutes)).strftime("%H:%M")
        msg = f"Committed — {minutes} minutes. I'll check back at {at_time}. Go."
        await send_fn(msg)
        await self._log_bot(msg)

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

        minutes = daily.get("commitment_minutes") or getattr(self._config, "followup_delay_min", 20)
        msg = f"{self._config.user_name} — {minutes} minutes are up. Did you do it or not?"
        view = _FollowupView(handler=self)
        await send_fn(msg, view=view)
        await self._log_bot(msg)

    # ── Response handlers (called by buttons or directly) ─────────────────────

    async def handle_done(self, send_fn: SendFn) -> None:
        """User completed the suggested task."""
        await self._state.update_daily(last_suggestion=None, last_suggestion_ts=None)
        msg = f"Done. That's how it's done, {self._config.user_name}. What's next?"
        await send_fn(msg)
        await self._log_bot(msg)

    async def handle_still_working(self, send_fn: SendFn) -> None:
        """User is still working on it — just acknowledge."""
        msg = f"Still working. Good — don't stop. Report back when it's done, {self._config.user_name}."
        await send_fn(msg)
        await self._log_bot(msg)

    async def handle_skipped(self, send_fn: SendFn) -> None:
        """User skipped the task — clear suggestion and acknowledge."""
        await self._state.update_daily(last_suggestion=None, last_suggestion_ts=None)
        msg = f"Skipped. It's still on the list, {self._config.user_name}. It doesn't disappear."
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


class TimerPickerView(discord.ui.View):
    """Button row for picking a commitment timer duration after a suggestion."""

    def __init__(self, handler: FollowupHandler, suggestion: str) -> None:
        super().__init__(timeout=300)  # 5 minutes to pick
        self._handler = handler
        self._suggestion = suggestion

    async def _set(self, interaction: discord.Interaction, minutes: int) -> None:
        self.stop()
        await interaction.response.defer()
        await self._handler.handle_timer_set(
            self._suggestion, minutes, interaction.followup.send
        )

    @discord.ui.button(label="10 min", style=discord.ButtonStyle.primary)
    async def ten(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set(interaction, 10)

    @discord.ui.button(label="20 min", style=discord.ButtonStyle.primary)
    async def twenty(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set(interaction, 20)

    @discord.ui.button(label="30 min", style=discord.ButtonStyle.primary)
    async def thirty(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set(interaction, 30)

    @discord.ui.button(label="45 min", style=discord.ButtonStyle.primary)
    async def forty_five(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set(interaction, 45)

    @discord.ui.button(label="No timer", style=discord.ButtonStyle.secondary)
    async def no_timer(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.defer()
        await interaction.followup.send("No timer set. Go.")
