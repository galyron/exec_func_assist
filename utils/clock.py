"""C16 — Clock abstraction.

All time-dependent logic in the bot uses Clock.now() rather than calling
datetime.now() directly. This makes the entire system testable and enables
the --debug time-simulation mode.

Usage:
    # Production
    clock = RealClock("Europe/Berlin")

    # Debug (1 real minute = 60 simulated minutes by default)
    start = datetime(2026, 3, 24, 7, 25, tzinfo=ZoneInfo("Europe/Berlin"))
    clock = DebugClock(start_time=start, multiplier=60.0)

    # Anywhere in the codebase
    now = clock.now()
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


class Clock(ABC):
    """Abstract clock — inject this everywhere time matters."""

    @abstractmethod
    def now(self) -> datetime:
        """Return the current datetime with timezone info."""


class RealClock(Clock):
    """Production clock: returns the actual current time."""

    def __init__(self, timezone: str) -> None:
        self._tz = ZoneInfo(timezone)

    def now(self) -> datetime:
        return datetime.now(self._tz)


class DebugClock(Clock):
    """Debug clock: simulates time advancing at a configurable multiplier.

    One real second passes as (multiplier / 60) simulated minutes.
    The default multiplier of 60 means 1 real minute = 1 simulated hour,
    allowing a full day cycle to be tested in ~24 real minutes.

    Args:
        start_time: The simulated start datetime (must be timezone-aware).
        multiplier: How many simulated minutes pass per real minute.
                    Default 60 → 1 real minute = 1 simulated hour.
    """

    def __init__(self, start_time: datetime, multiplier: float = 60.0) -> None:
        if start_time.tzinfo is None:
            raise ValueError("start_time must be timezone-aware.")
        self._start_sim = start_time
        self._start_real = datetime.now(start_time.tzinfo)
        self._multiplier = multiplier

    def now(self) -> datetime:
        elapsed_real_seconds = (
            datetime.now(self._start_sim.tzinfo) - self._start_real
        ).total_seconds()
        elapsed_sim_seconds = elapsed_real_seconds * self._multiplier
        return self._start_sim + timedelta(seconds=elapsed_sim_seconds)

    @property
    def multiplier(self) -> float:
        return self._multiplier
