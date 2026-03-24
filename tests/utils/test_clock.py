"""Tests for C16 — Clock abstraction."""

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from utils.clock import DebugClock, RealClock


TZ = ZoneInfo("Europe/Berlin")


# ── RealClock ─────────────────────────────────────────────────────────────────

def test_real_clock_returns_aware_datetime():
    clock = RealClock("Europe/Berlin")
    now = clock.now()
    assert now.tzinfo is not None


def test_real_clock_timezone_is_correct():
    clock = RealClock("Europe/Berlin")
    now = clock.now()
    assert str(now.tzinfo) == "Europe/Berlin"


def test_real_clock_advances():
    clock = RealClock("Europe/Berlin")
    t1 = clock.now()
    time.sleep(0.05)
    t2 = clock.now()
    assert t2 > t1


# ── DebugClock ────────────────────────────────────────────────────────────────

def _make_start(hour: int = 7, minute: int = 25) -> datetime:
    return datetime(2026, 3, 24, hour, minute, 0, tzinfo=TZ)


def test_debug_clock_starts_at_given_time():
    start = _make_start(7, 25)
    clock = DebugClock(start_time=start, multiplier=1.0)
    # Allow a small real-time delta
    assert abs((clock.now() - start).total_seconds()) < 0.5


def test_debug_clock_advances_faster_than_real_time():
    start = _make_start(7, 0)
    # multiplier=3600 → 1 real second = 1 simulated hour
    clock = DebugClock(start_time=start, multiplier=3600.0)
    time.sleep(0.1)  # 0.1 real seconds
    now = clock.now()
    # Should have advanced ~360 simulated seconds (6 minutes)
    elapsed_sim = (now - start).total_seconds()
    assert elapsed_sim >= 300  # at least 5 simulated minutes


def test_debug_clock_multiplier_one_is_real_time():
    start = _make_start(12, 0)
    clock = DebugClock(start_time=start, multiplier=1.0)
    time.sleep(0.1)
    elapsed = (clock.now() - start).total_seconds()
    # With multiplier=1, 0.1 real seconds ≈ 0.1 simulated seconds
    assert 0.05 < elapsed < 1.0


def test_debug_clock_requires_aware_datetime():
    naive = datetime(2026, 3, 24, 7, 30)
    with pytest.raises(ValueError, match="timezone-aware"):
        DebugClock(start_time=naive)


def test_debug_clock_preserves_timezone():
    start = _make_start(8, 0)
    clock = DebugClock(start_time=start, multiplier=60.0)
    assert clock.now().tzinfo == TZ


def test_debug_clock_exposes_multiplier():
    start = _make_start()
    clock = DebugClock(start_time=start, multiplier=120.0)
    assert clock.multiplier == 120.0
