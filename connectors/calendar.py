"""C4 — Calendar Connector.

Fetches events from Google Calendar API v3. Enumerates all calendars
the user has selected (calendarList.list), skips any in excluded_calendar_ids,
and queries events from each. Merges and sorts results.

Future extension point: additional ICS-feed adapters can produce the same
CalendarEvent / FreeWindow types without touching anything above this layer.

Standalone usage (lists all calendar IDs — useful for filling excluded_calendar_ids):
    docker compose exec bot python -m connectors.calendar
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from connectors.models import CalendarEvent, FreeWindow

log = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]
_DEFAULT_TOKEN_PATH = Path("secrets/google_token.json")


class CalendarConnector:
    """Async wrapper around the Google Calendar API v3.

    Args:
        token_path: Path to google_token.json (written by setup_calendar.py).
        timezone: IANA timezone string, e.g. "Europe/Berlin".
        excluded_calendar_ids: Calendar IDs to skip entirely.
        min_gap_min: Minimum free window duration to include in results.
    """

    def __init__(
        self,
        token_path: Path,
        timezone: str,
        excluded_calendar_ids: list[str],
        min_gap_min: int = 30,
    ) -> None:
        self._token_path = token_path
        self._tz = ZoneInfo(timezone)
        self._excluded = set(excluded_calendar_ids)
        self._min_gap_min = min_gap_min

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_events(self, target: Optional[datetime] = None) -> list[CalendarEvent]:
        """Return all events for the given date (default: today), sorted by start time.

        Returns [] on connector failure so the bot degrades gracefully.
        """
        try:
            events = await asyncio.to_thread(self._fetch_events, target)
            return sorted(events, key=lambda e: e.start)
        except Exception as exc:
            log.warning("Calendar connector failed: %s", exc)
            return []

    async def get_free_windows(
        self,
        events: list[CalendarEvent],
        work_start: datetime,
        work_end: datetime,
    ) -> list[FreeWindow]:
        """Compute free windows within work_start..work_end not covered by events."""
        return compute_free_windows(events, work_start, work_end, self._min_gap_min)

    async def list_calendars(self) -> list[dict]:
        """Return all calendars visible to the user.

        Useful for discovering IDs to add to excluded_calendar_ids.
        """
        try:
            return await asyncio.to_thread(self._fetch_calendar_list)
        except Exception as exc:
            log.warning("Could not list calendars: %s", exc)
            return []

    # ── Internal (sync, runs in thread) ──────────────────────────────────────

    def _fetch_events(self, target: Optional[datetime]) -> list[CalendarEvent]:
        from googleapiclient.discovery import build

        creds = self._load_credentials()
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)

        calendars = self._fetch_calendar_list(service=service)

        now = target or datetime.now(self._tz)
        day_start = datetime.combine(now.date(), time.min).replace(tzinfo=self._tz)
        day_end = day_start + timedelta(days=1)

        events: list[CalendarEvent] = []
        for cal in calendars:
            if cal["id"] in self._excluded:
                continue
            if not cal.get("selected", True):
                continue
            events.extend(self._fetch_calendar_events(service, cal, day_start, day_end))

        return events

    def _fetch_calendar_list(self, service=None) -> list[dict]:
        from googleapiclient.discovery import build

        if service is None:
            creds = self._load_credentials()
            service = build("calendar", "v3", credentials=creds, cache_discovery=False)

        items: list[dict] = []
        page_token = None
        while True:
            result = service.calendarList().list(pageToken=page_token).execute()
            items.extend(result.get("items", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return items

    def _fetch_calendar_events(
        self,
        service,
        cal: dict,
        day_start: datetime,
        day_end: datetime,
    ) -> list[CalendarEvent]:
        cal_id = cal["id"]
        cal_name = cal.get("summary", cal_id)

        try:
            result = service.events().list(
                calendarId=cal_id,
                timeMin=day_start.isoformat(),
                timeMax=day_end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                fields="items(id,summary,start,end,location)",
            ).execute()
        except Exception as exc:
            log.warning("Could not fetch events from calendar %r: %s", cal_name, exc)
            return []

        events = []
        for item in result.get("items", []):
            event = self._parse_event(item, cal_id, cal_name)
            if event is not None:
                events.append(event)
        return events

    def _parse_event(
        self, item: dict, cal_id: str, cal_name: str
    ) -> Optional[CalendarEvent]:
        start_raw = item.get("start", {})
        end_raw = item.get("end", {})
        is_all_day = "date" in start_raw and "dateTime" not in start_raw

        try:
            if is_all_day:
                start = datetime.fromisoformat(start_raw["date"]).replace(tzinfo=self._tz)
                end = datetime.fromisoformat(end_raw["date"]).replace(tzinfo=self._tz)
            else:
                start = datetime.fromisoformat(start_raw["dateTime"]).astimezone(self._tz)
                end = datetime.fromisoformat(end_raw["dateTime"]).astimezone(self._tz)
        except (KeyError, ValueError) as exc:
            log.debug("Skipping unparseable event %s: %s", item.get("id"), exc)
            return None

        return CalendarEvent(
            id=item["id"],
            calendar_id=cal_id,
            calendar_name=cal_name,
            title=item.get("summary", "(no title)"),
            start=start,
            end=end,
            is_all_day=is_all_day,
            location=item.get("location"),
        )

    def _load_credentials(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if not self._token_path.exists():
            raise FileNotFoundError(
                f"Google token not found at {self._token_path}. "
                "Run: python setup_calendar.py"
            )

        creds = Credentials.from_authorized_user_file(str(self._token_path), _SCOPES)
        if creds.expired and creds.refresh_token:
            log.info("Refreshing expired Google OAuth token.")
            creds.refresh(Request())
            self._token_path.write_text(creds.to_json())

        return creds


# ── Free window computation (pure, no I/O) ────────────────────────────────────

def compute_free_windows(
    events: list[CalendarEvent],
    work_start: datetime,
    work_end: datetime,
    min_gap_min: int,
) -> list[FreeWindow]:
    """Compute free windows within work_start..work_end not covered by timed events.

    All-day events are excluded — they don't block calendar time.
    """
    timed = sorted(
        [
            e for e in events
            if not e.is_all_day
            and e.end > work_start
            and e.start < work_end
        ],
        key=lambda e: e.start,
    )

    windows: list[FreeWindow] = []
    cursor = work_start

    for event in timed:
        gap_end = min(event.start, work_end)
        if gap_end > cursor:
            gap_min = int((gap_end - cursor).total_seconds() / 60)
            if gap_min >= min_gap_min:
                windows.append(FreeWindow(start=cursor, end=gap_end))
        cursor = max(cursor, event.end)
        if cursor >= work_end:
            break

    if cursor < work_end:
        gap_min = int((work_end - cursor).total_seconds() / 60)
        if gap_min >= min_gap_min:
            windows.append(FreeWindow(start=cursor, end=work_end))

    return windows


# ── Standalone verification ───────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from config import load_config

    async def _main() -> None:
        config = load_config()
        connector = CalendarConnector(
            token_path=_DEFAULT_TOKEN_PATH,
            timezone=config.timezone,
            excluded_calendar_ids=config.excluded_calendar_ids,
            min_gap_min=config.min_gap_for_nudge_min,
        )

        print("=== All calendars (use IDs for excluded_calendar_ids) ===")
        calendars = await connector.list_calendars()
        for cal in sorted(calendars, key=lambda c: c.get("summary", "")):
            selected = "✓" if cal.get("selected") else " "
            excluded = " [EXCLUDED]" if cal["id"] in config.excluded_calendar_ids else ""
            print(f"  [{selected}] {cal.get('summary', '?'):<40} {cal['id']}{excluded}")

        print("\n=== Today's events ===")
        events = await connector.get_events()
        if not events:
            print("  (none)")
        for e in events:
            tag = "[all-day]" if e.is_all_day else f"{e.start.strftime('%H:%M')}–{e.end.strftime('%H:%M')}"
            print(f"  {tag:<17} {e.title}  ({e.calendar_name})")

        print(f"\nTotal: {len(events)} events")

    asyncio.run(_main())
