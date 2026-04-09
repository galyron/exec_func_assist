"""C15 — Reminder Handler.

Manages user-requested timed reminders. Each reminder becomes an APScheduler
date job that fires at the specified time and sends the reminder text to Discord.

Supports multiple concurrent reminders (unique job IDs per reminder).
Reminders are stored in daily state for visibility in the LLM context.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import Config
from handlers.base import BaseHandler, SendFn
from state.manager import StateManager
from utils.clock import Clock

log = logging.getLogger(__name__)

_JOB_PREFIX = "reminder_"
_REMINDER_COUNTER_KEY = "reminder_counter"


class ReminderHandler(BaseHandler):
    """Schedules and fires user-requested timed reminders.

    Args:
        config: Bot configuration.
        state_manager: For storing active reminders in daily state.
        clock: Clock instance.
        get_send_fn: Callable returning channel.send (or None).
    """

    def __init__(
        self,
        config: Config,
        state_manager: StateManager,
        clock: Clock,
        get_send_fn,
    ) -> None:
        super().__init__(config, state_manager, clock)
        self._get_send_fn = get_send_fn
        self._apscheduler: Optional[AsyncIOScheduler] = None
        self._tz = ZoneInfo(config.timezone)

    def set_apscheduler(self, scheduler: AsyncIOScheduler) -> None:
        """Inject the APScheduler instance after creation."""
        self._apscheduler = scheduler

    async def schedule(
        self,
        text: str,
        run_at: datetime,
        send_fn: Optional[SendFn] = None,
    ) -> str:
        """Create a reminder that fires at run_at.

        Args:
            text: Reminder message text.
            run_at: When to fire.
            send_fn: Optional send function for confirmation message.

        Returns:
            The job ID for cancellation.
        """
        if self._apscheduler is None:
            log.warning("ReminderHandler.schedule() called before set_apscheduler()")
            return ""

        # Generate unique job ID
        daily = await self._state.get_daily()
        counter = daily.get(_REMINDER_COUNTER_KEY) or 0
        counter += 1
        job_id = f"{_JOB_PREFIX}{counter}"

        # Store in daily state
        reminders = list(daily.get("reminders") or [])
        reminders.append({
            "id": job_id,
            "text": text,
            "fire_at": run_at.isoformat(),
            "created_at": self._clock.now().isoformat(),
        })
        await self._state.update_daily(
            reminders=reminders,
            reminder_counter=counter,
        )

        # Schedule the APScheduler job
        self._apscheduler.add_job(
            self._fire,
            trigger="date",
            run_date=run_at,
            id=job_id,
            replace_existing=True,
            misfire_grace_time=300,  # 5 min grace for reminders
            args=[job_id, text],
        )
        log.info("Reminder scheduled: %r at %s (job=%s)", text, run_at.strftime("%H:%M"), job_id)
        return job_id

    async def cancel(self, job_id: str) -> bool:
        """Cancel a specific reminder by job ID."""
        if self._apscheduler is None:
            return False
        try:
            self._apscheduler.remove_job(job_id)
        except JobLookupError:
            pass

        # Remove from state
        daily = await self._state.get_daily()
        reminders = [r for r in (daily.get("reminders") or []) if r["id"] != job_id]
        await self._state.update_daily(reminders=reminders)
        return True

    async def cancel_all(self) -> int:
        """Cancel all pending reminders. Returns count cancelled."""
        daily = await self._state.get_daily()
        reminders = daily.get("reminders") or []
        count = 0
        for r in reminders:
            if self._apscheduler:
                try:
                    self._apscheduler.remove_job(r["id"])
                except JobLookupError:
                    pass
            count += 1
        await self._state.update_daily(reminders=[])
        return count

    async def get_active(self) -> list[dict]:
        """Return list of active reminders."""
        daily = await self._state.get_daily()
        return list(daily.get("reminders") or [])

    async def _fire(self, job_id: str, text: str) -> None:
        """Called by APScheduler when a reminder fires."""
        send_fn = self._get_send_fn()
        if send_fn is None:
            log.warning("Reminder %s fired but Discord channel unavailable.", job_id)
            return

        msg = f"**Reminder:** {text}"
        await send_fn(msg)
        await self._log_bot(msg)

        # Remove from state
        daily = await self._state.get_daily()
        reminders = [r for r in (daily.get("reminders") or []) if r["id"] != job_id]
        await self._state.update_daily(reminders=reminders)
        log.info("Reminder fired and cleaned up: %s", job_id)


# ── Intent detection helpers (used by on_demand.py) ──────────────────────────

# Patterns for "remind me at 14:30 about X" / "reminder at 21:30: X"
_REMIND_AT_PATTERN = re.compile(
    r'remind(?:er|(?:\s+me))?\s+'
    r'(?:at\s+)?'
    r'(\d{1,2})[:\.](\d{2})\s*'
    r'(?:[-:—]\s*|(?:about|to|that)\s+)?'
    r'(.+)',
    re.IGNORECASE | re.DOTALL,
)

# "remind me tomorrow at 09:45: X"
_REMIND_TOMORROW_PATTERN = re.compile(
    r'remind(?:er|(?:\s+me))?\s+'
    r'(?:tomorrow\s+)?'
    r'(?:at\s+)?'
    r'(\d{1,2})[:\.](\d{2})\s*'
    r'(?:[-:—]\s*|(?:about|to|that)\s+)?'
    r'(.+)',
    re.IGNORECASE | re.DOTALL,
)

# "remind me on friday at 13:00: X" / "reminder friday 13:00 X"
_REMIND_DAY_PATTERN = re.compile(
    r'remind(?:er|(?:\s+me))?\s+'
    r'(?:on\s+)?'
    r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+'
    r'(?:at\s+)?'
    r'(\d{1,2})[:\.](\d{2})\s*'
    r'(?:[-:—]\s*|(?:about|to|that)\s+)?'
    r'(.+)',
    re.IGNORECASE | re.DOTALL,
)

_DAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def parse_reminder(text: str, now: datetime, tz: ZoneInfo) -> Optional[tuple[str, datetime]]:
    """Try to parse a reminder request from user text.

    Returns (reminder_text, fire_at_datetime) or None if not a reminder.
    """
    # "remind me on <day> at HH:MM"
    m = _REMIND_DAY_PATTERN.match(text.strip())
    if m:
        day_name = m.group(1).lower()
        hour, minute = int(m.group(2)), int(m.group(3))
        reminder_text = m.group(4).strip()
        if not reminder_text:
            return None
        target_dow = _DAYS[day_name]
        current_dow = now.weekday()
        days_ahead = (target_dow - current_dow) % 7
        if days_ahead == 0 and (hour < now.hour or (hour == now.hour and minute <= now.minute)):
            days_ahead = 7  # next week if today's time has passed
        fire_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days_ahead)
        return reminder_text, fire_at

    # "remind me tomorrow at HH:MM" — check before generic pattern
    stripped = text.strip()
    if re.match(r'remind.*tomorrow', stripped, re.IGNORECASE):
        m = _REMIND_TOMORROW_PATTERN.match(stripped)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
            reminder_text = m.group(3).strip()
            if not reminder_text:
                return None
            fire_at = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            return reminder_text, fire_at

    # "remind me at HH:MM: X" — today (or tomorrow if time has passed)
    m = _REMIND_AT_PATTERN.match(text.strip())
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        reminder_text = m.group(3).strip()
        if not reminder_text:
            return None
        fire_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if fire_at <= now:
            fire_at += timedelta(days=1)  # tomorrow if time already passed
        return reminder_text, fire_at

    return None
