"""Tests for C4 — Calendar Connector.

The Google API client (googleapiclient) is not called in these tests.
compute_free_windows is a pure function and is tested exhaustively.
CalendarConnector._parse_event is tested via a thin wrapper.
The async public interface is tested with a mocked _fetch_events.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo
from pathlib import Path

import pytest

from connectors.calendar import CalendarConnector, compute_free_windows
from connectors.models import CalendarEvent, FreeWindow

TZ = ZoneInfo("Europe/Berlin")


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def connector(tmp_path):
    return CalendarConnector(
        token_path=tmp_path / "google_token.json",
        timezone="Europe/Berlin",
        excluded_calendar_ids=["birthdays@group.v.calendar.google.com"],
        min_gap_min=30,
    )


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, 25, hour, minute, tzinfo=TZ)


def make_event(
    title: str,
    start_h: int,
    end_h: int,
    *,
    cal_id: str = "primary",
    all_day: bool = False,
) -> CalendarEvent:
    return CalendarEvent(
        id=f"{title}-id",
        calendar_id=cal_id,
        calendar_name="Test Calendar",
        title=title,
        start=dt(start_h),
        end=dt(end_h),
        is_all_day=all_day,
    )


# ── compute_free_windows ──────────────────────────────────────────────────────

def test_no_events_returns_full_window():
    windows = compute_free_windows([], dt(9), dt(16), min_gap_min=30)
    assert len(windows) == 1
    assert windows[0].start == dt(9)
    assert windows[0].end == dt(16)
    assert windows[0].duration_min == 7 * 60


def test_event_at_start_leaves_window_after():
    events = [make_event("Meeting", 9, 10)]
    windows = compute_free_windows(events, dt(9), dt(16), min_gap_min=30)
    assert len(windows) == 1
    assert windows[0].start == dt(10)
    assert windows[0].end == dt(16)


def test_event_at_end_leaves_window_before():
    events = [make_event("Call", 15, 16)]
    windows = compute_free_windows(events, dt(9), dt(16), min_gap_min=30)
    assert len(windows) == 1
    assert windows[0].start == dt(9)
    assert windows[0].end == dt(15)


def test_event_in_middle_creates_two_windows():
    events = [make_event("Standup", 10, 11)]
    windows = compute_free_windows(events, dt(9), dt(16), min_gap_min=30)
    assert len(windows) == 2
    assert windows[0] == FreeWindow(start=dt(9), end=dt(10))
    assert windows[1] == FreeWindow(start=dt(11), end=dt(16))


def test_small_gap_below_minimum_excluded():
    # Gap of 20 min between two events is below the 30-min threshold → no window there
    events = [
        make_event("Morning meeting", 9, 10),
        CalendarEvent("id2", "c", "C", "Afternoon block", dt(10, 20), dt(16), False),
    ]
    windows = compute_free_windows(events, dt(9), dt(16), min_gap_min=30)
    # The 20-min gap (10:00–10:20) should not appear
    for w in windows:
        assert w.duration_min >= 30


def test_gap_exactly_at_minimum_included():
    # Meeting 9:30–10:00 → 30-min gap before (9:00–9:30) should be included
    events = [CalendarEvent("id", "c", "C", "Meeting", dt(9, 30), dt(10), False)]
    windows = compute_free_windows(events, dt(9), dt(16), min_gap_min=30)
    assert windows[0].start == dt(9)
    assert windows[0].end == dt(9, 30)
    assert windows[0].duration_min == 30


def test_all_day_event_does_not_block_windows():
    events = [make_event("Holiday", 0, 0, all_day=True)]
    windows = compute_free_windows(events, dt(9), dt(16), min_gap_min=30)
    assert len(windows) == 1
    assert windows[0].duration_min == 7 * 60


def test_overlapping_events_handled():
    # Two events overlap: 10-12 and 11-13 → blocked 10-13
    events = [
        make_event("A", 10, 12),
        make_event("B", 11, 13),
    ]
    windows = compute_free_windows(events, dt(9), dt(16), min_gap_min=30)
    starts = [w.start for w in windows]
    ends = [w.end for w in windows]
    assert dt(9) in starts   # gap before first event
    assert dt(13) in starts  # gap after overlapping block


def test_full_day_blocked_returns_no_windows():
    events = [make_event("All day meeting", 9, 16)]
    windows = compute_free_windows(events, dt(9), dt(16), min_gap_min=30)
    assert windows == []


def test_event_outside_work_hours_ignored():
    events = [make_event("Early call", 7, 8), make_event("Late call", 17, 18)]
    windows = compute_free_windows(events, dt(9), dt(16), min_gap_min=30)
    assert len(windows) == 1
    assert windows[0].duration_min == 7 * 60


# ── _parse_event ──────────────────────────────────────────────────────────────

def test_parse_timed_event(connector):
    item = {
        "id": "evt1",
        "summary": "Team sync",
        "start": {"dateTime": "2026-03-25T10:00:00+01:00"},
        "end": {"dateTime": "2026-03-25T11:00:00+01:00"},
    }
    event = connector._parse_event(item, "primary", "My Calendar")
    assert event is not None
    assert event.title == "Team sync"
    assert event.is_all_day is False
    assert event.start.hour == 10
    assert (event.end - event.start).seconds == 3600


def test_parse_all_day_event(connector):
    item = {
        "id": "evt2",
        "summary": "Public Holiday",
        "start": {"date": "2026-03-25"},
        "end": {"date": "2026-03-26"},
    }
    event = connector._parse_event(item, "primary", "My Calendar")
    assert event is not None
    assert event.is_all_day is True
    assert event.title == "Public Holiday"


def test_parse_event_missing_summary_uses_fallback(connector):
    item = {
        "id": "evt3",
        "start": {"dateTime": "2026-03-25T14:00:00+01:00"},
        "end": {"dateTime": "2026-03-25T15:00:00+01:00"},
    }
    event = connector._parse_event(item, "primary", "My Calendar")
    assert event.title == "(no title)"


def test_parse_event_bad_datetime_returns_none(connector):
    item = {
        "id": "evt4",
        "summary": "Broken",
        "start": {"dateTime": "not-a-date"},
        "end": {"dateTime": "also-not-a-date"},
    }
    event = connector._parse_event(item, "primary", "My Calendar")
    assert event is None


# ── get_events: excluded calendars ───────────────────────────────────────────

async def test_get_events_returns_empty_on_failure(connector):
    with patch.object(connector, "_fetch_events", side_effect=Exception("no token")):
        events = await connector.get_events()
    assert events == []


async def test_get_events_sorted_by_start(connector):
    raw = [
        make_event("B", 11, 12),
        make_event("A", 9, 10),
        make_event("C", 14, 15),
    ]
    with patch("asyncio.to_thread", new=AsyncMock(return_value=raw)):
        events = await connector.get_events()
    assert [e.title for e in events] == ["A", "B", "C"]


# ── create_event ──────────────────────────────────────────────────────────────

async def test_create_event_returns_event_id(connector):
    start = dt(14)
    end = dt(15)
    with patch.object(connector, "_insert_event", return_value="new-event-id") as mock_insert:
        event_id = await connector.create_event("Dentist", start, end, "primary")
    assert event_id == "new-event-id"
    mock_insert.assert_called_once_with("Dentist", start, end, "primary")


async def test_create_event_defaults_to_primary(connector):
    start = dt(10)
    end = dt(11)
    with patch.object(connector, "_insert_event", return_value="eid") as mock_insert:
        await connector.create_event("Standup", start, end)
    _, _, _, cal_id = mock_insert.call_args[0]
    assert cal_id == "primary"


async def test_create_event_propagates_error(connector):
    with patch.object(connector, "_insert_event", side_effect=RuntimeError("API error")):
        with pytest.raises(RuntimeError, match="API error"):
            await connector.create_event("Broken", dt(10), dt(11))
