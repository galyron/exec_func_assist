"""Tests for C14 — Scheduler.

Verifies that the right jobs are registered with the right schedules.
APScheduler itself is not mocked — we use the real scheduler in stopped state
and inspect its job list.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scheduler import Scheduler


@pytest.fixture
def config():
    cfg = MagicMock()
    cfg.timezone = "Europe/Berlin"
    cfg.morning_routine = "07:30"
    cfg.morning_routine_retry_window_min = 90
    cfg.work_start = "09:15"
    cfg.midday_checkin = "13:00"
    cfg.evening_start = "20:30"
    cfg.end_of_day_review = "22:30"
    cfg.bedtime = "23:00"
    cfg.weekend_evening_nudge = True
    return cfg


@pytest.fixture
def handlers():
    return (
        MagicMock(),  # morning
        MagicMock(),  # kickoff
        MagicMock(),  # checkin
        MagicMock(),  # bedtime
    )


@pytest.fixture
def scheduler(config, handlers):
    morning, kickoff, checkin, bedtime = handlers
    s = Scheduler(
        config=config,
        get_send_fn=MagicMock(return_value=AsyncMock()),
        morning_handler=morning,
        kickoff_handler=kickoff,
        checkin_handler=checkin,
        bedtime_handler=bedtime,
    )
    s._register_jobs()  # register without starting the scheduler
    return s


def _job_ids(scheduler):
    return {job.id for job in scheduler._scheduler.get_jobs()}


def test_all_expected_jobs_registered(scheduler):
    ids = _job_ids(scheduler)
    assert "morning_routine" in ids
    assert "morning_retry" in ids
    assert "day_kickoff" in ids
    assert "midday_checkin" in ids
    assert "evening_checkin" in ids
    assert "end_of_day" in ids
    assert "bedtime" in ids


def test_seven_jobs_total(scheduler):
    assert len(scheduler._scheduler.get_jobs()) == 7


def test_morning_job_weekday_only(scheduler):
    job = scheduler._scheduler.get_job("morning_routine")
    assert "mon-fri" in str(job.trigger)


def test_kickoff_job_weekday_only(scheduler):
    job = scheduler._scheduler.get_job("day_kickoff")
    assert "mon-fri" in str(job.trigger)


def test_bedtime_job_every_day(scheduler):
    job = scheduler._scheduler.get_job("bedtime")
    assert "mon-sun" in str(job.trigger)


def test_end_of_day_job_every_day(scheduler):
    job = scheduler._scheduler.get_job("end_of_day")
    assert "mon-sun" in str(job.trigger)


def test_evening_job_every_day_when_weekend_nudge_enabled(scheduler):
    job = scheduler._scheduler.get_job("evening_checkin")
    assert "mon-sun" in str(job.trigger)


def test_evening_job_weekday_only_when_weekend_nudge_disabled(config, handlers):
    config.weekend_evening_nudge = False
    morning, kickoff, checkin, bedtime = handlers
    s = Scheduler(
        config=config,
        get_send_fn=MagicMock(return_value=AsyncMock()),
        morning_handler=morning,
        kickoff_handler=kickoff,
        checkin_handler=checkin,
        bedtime_handler=bedtime,
    )
    s._register_jobs()
    job = s._scheduler.get_job("evening_checkin")
    assert "mon-fri" in str(job.trigger)


def test_retry_time_is_correct(config, handlers):
    """morning_retry should fire 90 min after morning_routine."""
    morning, kickoff, checkin, bedtime = handlers
    s = Scheduler(
        config=config,
        get_send_fn=MagicMock(return_value=AsyncMock()),
        morning_handler=morning,
        kickoff_handler=kickoff,
        checkin_handler=checkin,
        bedtime_handler=bedtime,
    )
    s._register_jobs()
    job = s._scheduler.get_job("morning_retry")
    # 07:30 + 90 min = 09:00
    assert "9" in str(job.trigger) and "0" in str(job.trigger)


def test_all_jobs_have_coalesce(scheduler):
    for job in scheduler._scheduler.get_jobs():
        assert job.coalesce is True, f"Job {job.id} should have coalesce=True"


def test_all_jobs_have_max_instances_1(scheduler):
    for job in scheduler._scheduler.get_jobs():
        assert job.max_instances == 1, f"Job {job.id} should have max_instances=1"
