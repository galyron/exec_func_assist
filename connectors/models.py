"""Shared data models for connector output.

These types are the contract between connectors (C3, C4) and the
Context Assembler (C5). Adding a new calendar source in the future
only requires producing the same CalendarEvent / FreeWindow types —
everything above this layer is unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Task:
    """A single actionable item from Joplin.

    Represents either a standalone todo note (type_=2) or an unchecked
    checklist item within a regular note body.
    """
    id: str                 # note ID for todos; "noteId:position" for checklist items
    note_id: str            # always the Joplin note ID (parent note for checklist items)
    title: str              # task text
    notebook: str           # parent folder name (used as project/area label)
    notebook_id: str
    tags: list[str]         # inline tags found in text, e.g. ["[high]", "[couch]"]
    is_high_priority: bool  # True if "[high]" in tags
    position: int           # order within notebook/note (lower = earlier/higher priority)
    updated_time: int       # Joplin unix ms timestamp
    is_checklist_item: bool = False          # True if from a note checklist (not a todo note)
    checklist_item_text: Optional[str] = None  # raw text for checklist matching on write-back


@dataclass
class CalendarEvent:
    """A single calendar event, normalised across all calendar sources."""
    id: str
    calendar_id: str
    calendar_name: str
    title: str
    start: datetime         # timezone-aware
    end: datetime           # timezone-aware
    is_all_day: bool
    location: Optional[str] = None


@dataclass
class FreeWindow:
    """A contiguous block of free time within the work day."""
    start: datetime         # timezone-aware
    end: datetime           # timezone-aware

    @property
    def duration_min(self) -> int:
        return int((self.end - self.start).total_seconds() / 60)

    def __repr__(self) -> str:
        return (
            f"FreeWindow({self.start.strftime('%H:%M')}–{self.end.strftime('%H:%M')}, "
            f"{self.duration_min}min)"
        )
