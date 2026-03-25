"""C14 — Scheduler.

Registers all timed jobs with APScheduler AsyncIOScheduler.
All jobs: coalesce=True, max_instances=1, misfire_grace_time=300s.

Weekend suppression is built into CronTrigger day_of_week filters rather
than runtime checks — the scheduler simply doesn't fire work-mode jobs
on Saturday/Sunday.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import Config
from handlers.bedtime import BedtimeHandler
from handlers.checkin import CheckinHandler, CheckinType
from handlers.kickoff import KickoffHandler
from handlers.morning import MorningRoutineHandler

log = logging.getLogger(__name__)

SendFn = Callable[..., Awaitable[Any]]


class Scheduler:
    """Registers and manages all APScheduler jobs.

    Args:
        config: Bot configuration (schedule times, timezone, flags).
        get_send_fn: Callable that returns the current channel.send (or None
            if the channel isn't available yet). Called lazily at fire time.
        morning_handler: C8
        kickoff_handler: C9
        checkin_handler: C10
        bedtime_handler: C11
    """

    def __init__(
        self,
        config: Config,
        get_send_fn: Callable[[], Optional[SendFn]],
        morning_handler: MorningRoutineHandler,
        kickoff_handler: KickoffHandler,
        checkin_handler: CheckinHandler,
        bedtime_handler: BedtimeHandler,
    ) -> None:
        self._config = config
        self._get_send_fn = get_send_fn
        self._morning = morning_handler
        self._kickoff = kickoff_handler
        self._checkin = checkin_handler
        self._bedtime = bedtime_handler
        self._scheduler = AsyncIOScheduler(timezone=config.timezone)

    def start(self) -> None:
        """Register all jobs and start the scheduler."""
        self._register_jobs()
        self._scheduler.start()
        log.info("Scheduler started — %d jobs registered.", len(self._scheduler.get_jobs()))

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        log.info("Scheduler stopped.")

    # ── Job registration ──────────────────────────────────────────────────────

    def _register_jobs(self) -> None:
        cfg = self._config

        # Morning routine — weekdays only
        h, m = _hhmm(cfg.morning_routine)
        self._add("morning_routine",
                  CronTrigger(hour=h, minute=m, day_of_week="mon-fri"),
                  self._fire_morning)

        # Morning retry — weekdays only, N minutes after morning_routine
        rh, rm = _add_minutes(h, m, cfg.morning_routine_retry_window_min)
        self._add("morning_retry",
                  CronTrigger(hour=rh, minute=rm, day_of_week="mon-fri"),
                  self._fire_morning_retry)

        # Day kick-off — weekdays only
        h, m = _hhmm(cfg.work_start)
        self._add("day_kickoff",
                  CronTrigger(hour=h, minute=m, day_of_week="mon-fri"),
                  self._fire_kickoff)

        # Midday check-in — weekdays only
        h, m = _hhmm(cfg.midday_checkin)
        self._add("midday_checkin",
                  CronTrigger(hour=h, minute=m, day_of_week="mon-fri"),
                  self._fire_midday)

        # Evening check-in — every day if weekend nudge enabled, else weekdays
        h, m = _hhmm(cfg.evening_start)
        dow = "mon-sun" if cfg.weekend_evening_nudge else "mon-fri"
        self._add("evening_checkin",
                  CronTrigger(hour=h, minute=m, day_of_week=dow),
                  self._fire_evening)

        # End-of-day review — every day
        h, m = _hhmm(cfg.end_of_day_review)
        self._add("end_of_day",
                  CronTrigger(hour=h, minute=m, day_of_week="mon-sun"),
                  self._fire_end_of_day)

        # Bedtime reminder — every day (exempt from off_today suppression)
        h, m = _hhmm(cfg.bedtime)
        self._add("bedtime",
                  CronTrigger(hour=h, minute=m, day_of_week="mon-sun"),
                  self._fire_bedtime)

    def _add(self, job_id: str, trigger: CronTrigger, func: Callable) -> None:
        self._scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=300,  # fire up to 5 min late rather than skip
        )
        log.debug("Registered job: %s  trigger: %s", job_id, trigger)

    # ── Job callables ─────────────────────────────────────────────────────────

    async def _fire_morning(self) -> None:
        if send_fn := self._get_send_fn():
            await self._morning.fire(send_fn)
        else:
            log.warning("morning_routine fired but Discord channel unavailable — skipped.")

    async def _fire_morning_retry(self) -> None:
        if send_fn := self._get_send_fn():
            await self._morning.fire_retry(send_fn)
        else:
            log.warning("morning_retry fired but Discord channel unavailable — skipped.")

    async def _fire_kickoff(self) -> None:
        if send_fn := self._get_send_fn():
            await self._kickoff.fire(send_fn)
        else:
            log.warning("day_kickoff fired but Discord channel unavailable — skipped.")

    async def _fire_midday(self) -> None:
        if send_fn := self._get_send_fn():
            await self._checkin.fire(CheckinType.MIDDAY, send_fn)
        else:
            log.warning("midday_checkin fired but Discord channel unavailable — skipped.")

    async def _fire_evening(self) -> None:
        if send_fn := self._get_send_fn():
            await self._checkin.fire(CheckinType.EVENING, send_fn)
        else:
            log.warning("evening_checkin fired but Discord channel unavailable — skipped.")

    async def _fire_end_of_day(self) -> None:
        if send_fn := self._get_send_fn():
            await self._bedtime.fire_end_of_day(send_fn)
        else:
            log.warning("end_of_day fired but Discord channel unavailable — skipped.")

    async def _fire_bedtime(self) -> None:
        if send_fn := self._get_send_fn():
            await self._bedtime.fire_bedtime(send_fn)
        else:
            log.warning("bedtime fired but Discord channel unavailable — skipped.")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hhmm(hhmm: str) -> tuple[int, int]:
    h, m = hhmm.split(":")
    return int(h), int(m)


def _add_minutes(h: int, m: int, minutes: int) -> tuple[int, int]:
    total = h * 60 + m + minutes
    return (total // 60) % 24, total % 60
