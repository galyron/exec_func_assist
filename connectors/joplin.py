"""C3 — Joplin Connector.

Reads and writes tasks via the Joplin REST API (Web Clipper / CLI server).

Only notes in the configured `notebook` (default: "00_TODO") are considered.
Notes in all other notebooks are ignored.

Handles two task sources:
  - Standalone todo notes (type_=2, not todo_completed)
  - Unchecked checklist items within regular note bodies

Tags are extracted from note titles and bodies. Both bracket syntax and natural
language variants are recognised and normalised to canonical tag names:
  [today], [urgent], [this-week], [high], [low-energy], [easy]

See README for the full tag reference.

Standalone usage (for verification):
    docker compose exec bot python -m connectors.joplin
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

import aiohttp

from connectors.models import Task

log = logging.getLogger(__name__)

_CHECKLIST_RE = re.compile(r"^- \[( |x)\] (.+)$", re.MULTILINE)
_NOTE_FIELDS = "id,title,body,parent_id,is_todo,todo_completed,order,updated_time"
_FOLDER_FIELDS = "id,title"

# ── Tag rules: (pattern, canonical_tag) ──────────────────────────────────────
# Each rule matches one or more natural-language variants and maps them to a
# single canonical tag. Rules are evaluated in order; all matching rules fire
# (a note can carry multiple tags).

_TAG_RULES: list[tuple[re.Pattern, str]] = [
    # [today] — must be done today
    (re.compile(
        r"\btoday\b"
        r"|by\s+eod\b|by\s+eob\b|\beod\b|\beob\b"
        r"|do\s+it\s+today|must\s+(do\s+)?today"
        r"|urgent/today",
        re.IGNORECASE,
    ), "[today]"),
    # [urgent] — drop everything
    (re.compile(r"\burgent\b|\basap\b", re.IGNORECASE), "[urgent]"),
    # [this-week] — sometime this week
    (re.compile(
        r"\bthis\s+week\b|by\s+eow\b|\beow\b|by\s+end\s+of\s+(the\s+)?week",
        re.IGNORECASE,
    ), "[this-week]"),
    # [high] — important, not time-bound
    (re.compile(r"\[high\]|\bhigh\s+priority\b|\bimportant\b", re.IGNORECASE), "[high]"),
    # [low-energy] — can do when tired / on the couch
    (re.compile(r"\[low-energy\]|\[couch\]|\blow[\s-]energy\b|\bcouch\b", re.IGNORECASE), "[low-energy]"),
    # [easy] — quick win
    (re.compile(r"\[easy\]|\beasy\b|\bquick\s+win\b|\bquick\b", re.IGNORECASE), "[easy]"),
]


class JoplinConnector:
    """Async client for the Joplin Data REST API.

    Args:
        host: Joplin API host (Docker service name "joplin" or IP).
        port: Joplin API port (default 41184).
        token: Joplin API token (set in .env as JOPLIN_API_TOKEN).
        notebook: Only tasks from this notebook name are returned (default "00_TODO").
    """

    def __init__(self, host: str, port: int, token: str, notebook: str = "00_TODO") -> None:
        self._base = f"http://{host}:{port}"
        self._token = token
        self._notebook = notebook
        self._todo_folder_id: Optional[str] = None  # cached after first get_tasks()

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_tasks(self) -> list[Task]:
        """Return all uncompleted tasks from the configured notebook.

        Returns an empty list on connector failure (degraded mode) so the
        bot can continue operating without Joplin context.
        """
        try:
            async with aiohttp.ClientSession() as session:
                folders = await self._get_all(session, "/folders", fields=_FOLDER_FIELDS)
                notes = await self._get_all(session, "/notes", fields=_NOTE_FIELDS)
        except Exception as exc:
            log.warning("Joplin connector unavailable: %s", exc)
            return []

        folder_map = {f["id"]: f["title"] for f in folders}

        # Find the configured notebook ID; warn and return [] if not found.
        todo_folder_id = next(
            (fid for fid, title in folder_map.items() if title == self._notebook),
            None,
        )
        if todo_folder_id is None:
            log.warning(
                "Joplin notebook %r not found. Available: %s",
                self._notebook,
                list(folder_map.values()),
            )
            return []

        self._todo_folder_id = todo_folder_id  # cache for write operations

        tasks: list[Task] = []

        for note in notes:
            if note.get("parent_id") != todo_folder_id:
                continue

            notebook_name = folder_map.get(note["parent_id"], "Unknown")

            if note.get("is_todo"):
                if not note.get("todo_completed"):
                    tasks.append(self._todo_to_task(note, notebook_name, todo_folder_id))
            else:
                body = note.get("body") or ""
                for pos, (checked, text) in enumerate(self._parse_checklist(body)):
                    if not checked:
                        item_text = text.strip()
                        tags = self._extract_tags(item_text)
                        tasks.append(Task(
                            id=f"{note['id']}:{pos}",
                            note_id=note["id"],
                            title=item_text,
                            notebook=notebook_name,
                            notebook_id=todo_folder_id,
                            tags=tags,
                            is_high_priority="[high]" in tags,
                            position=pos,
                            updated_time=note.get("updated_time", 0),
                            is_checklist_item=True,
                            checklist_item_text=item_text,
                        ))

        return tasks

    async def mark_done(self, task: Task) -> bool:
        """Mark a task as completed in Joplin. Returns True on success.

        Standalone todos: sets todo_completed timestamp via PUT /notes/:id.
        Checklist items: fetches note body, replaces `- [ ] text` with `- [x] text`,
        then PUTs the updated body back.
        """
        try:
            async with aiohttp.ClientSession() as session:
                if task.is_checklist_item:
                    return await self._mark_checklist_item_done(session, task)
                else:
                    await self._put(session, f"/notes/{task.note_id}", {
                        "todo_completed": int(time.time() * 1000),
                    })
                    return True
        except Exception as exc:
            log.warning("Joplin mark_done failed for %r: %s", task.id, exc)
            return False

    async def create_task(self, title: str) -> Optional[str]:
        """Create a new todo note in the configured notebook. Returns the new note id or None."""
        folder_id = await self._ensure_folder_id()
        if folder_id is None:
            log.warning("create_task: cannot determine todo folder id")
            return None
        try:
            async with aiohttp.ClientSession() as session:
                data = await self._post(session, "/notes", {
                    "title": title,
                    "is_todo": 1,
                    "parent_id": folder_id,
                })
                return data.get("id")
        except Exception as exc:
            log.warning("Joplin create_task failed for %r: %s", title, exc)
            return None

    async def ping(self) -> bool:
        """Return True if the Joplin API is reachable."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._base}/ping", params={"token": self._token}, timeout=aiohttp.ClientTimeout(total=3)
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _todo_to_task(self, note: dict, notebook: str, notebook_id: str) -> Task:
        title = note.get("title") or "Untitled"
        body = note.get("body") or ""
        tags = list(set(self._extract_tags(title) + self._extract_tags(body)))
        return Task(
            id=note["id"],
            note_id=note["id"],
            title=title,
            notebook=notebook,
            notebook_id=notebook_id,
            tags=tags,
            is_high_priority="[high]" in tags,
            position=int(note.get("order") or 0),
            updated_time=note.get("updated_time", 0),
            is_checklist_item=False,
            checklist_item_text=None,
        )

    async def _get_all(
        self, session: aiohttp.ClientSession, path: str, **params
    ) -> list[dict]:
        """Fetch all pages for a Joplin endpoint."""
        items: list[dict] = []
        page = 1
        while True:
            data = await self._get(session, path, page=page, limit=100, **params)
            items.extend(data.get("items", []))
            if not data.get("has_more"):
                break
            page += 1
        return items

    async def _get(
        self, session: aiohttp.ClientSession, path: str, **params
    ) -> dict:
        params["token"] = self._token
        async with session.get(
            f"{self._base}{path}",
            params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _mark_checklist_item_done(
        self, session: aiohttp.ClientSession, task: Task
    ) -> bool:
        """Fetch note body, check off the matching item, PUT updated body."""
        note_data = await self._get(session, f"/notes/{task.note_id}", fields="id,body")
        body = note_data.get("body") or ""
        item_text = task.checklist_item_text or task.title
        # Replace first matching unchecked item
        new_body, count = re.subn(
            r"^(- \[ \] )" + re.escape(item_text) + r"$",
            r"- [x] " + item_text,
            body,
            count=1,
            flags=re.MULTILINE,
        )
        if count == 0:
            log.warning(
                "mark_done: checklist item %r not found in note %s", item_text, task.note_id
            )
            return False
        await self._put(session, f"/notes/{task.note_id}", {"body": new_body})
        return True

    async def _ensure_folder_id(self) -> Optional[str]:
        """Return the cached todo folder id, fetching folders if not yet cached."""
        if self._todo_folder_id:
            return self._todo_folder_id
        try:
            async with aiohttp.ClientSession() as session:
                folders = await self._get_all(session, "/folders", fields=_FOLDER_FIELDS)
            folder_id = next(
                (f["id"] for f in folders if f["title"] == self._notebook), None
            )
            self._todo_folder_id = folder_id
            return folder_id
        except Exception as exc:
            log.warning("_ensure_folder_id failed: %s", exc)
            return None

    async def _put(
        self, session: aiohttp.ClientSession, path: str, data: dict
    ) -> dict:
        params = {"token": self._token}
        async with session.put(
            f"{self._base}{path}",
            params=params,
            json=data,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _post(
        self, session: aiohttp.ClientSession, path: str, data: dict
    ) -> dict:
        params = {"token": self._token}
        async with session.post(
            f"{self._base}{path}",
            params=params,
            json=data,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    # ── Static parsers (testable in isolation) ────────────────────────────────

    @staticmethod
    def _parse_checklist(body: str) -> list[tuple[bool, str]]:
        """Return (is_checked, text) for each checklist item in a note body."""
        return [
            (m.group(1) == "x", m.group(2))
            for m in _CHECKLIST_RE.finditer(body)
        ]

    @staticmethod
    def _extract_tags(text: str) -> list[str]:
        """Return deduplicated canonical tags found in text."""
        found: list[str] = []
        for pattern, canonical in _TAG_RULES:
            if pattern.search(text) and canonical not in found:
                found.append(canonical)
        return found


# ── Standalone verification ───────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from config import load_config

    async def _main() -> None:
        config = load_config()
        connector = JoplinConnector(
            host=config.joplin_host,
            port=config.joplin_api_port,
            token=config.joplin_api_token,
            notebook=config.todo_notebook,
        )

        if not await connector.ping():
            print("ERROR: Joplin API not reachable. Is the joplin container running?")
            return

        tasks = await connector.get_tasks()
        if not tasks:
            print(f"No uncompleted tasks found in notebook '{config.todo_notebook}'.")
            return

        for task in sorted(tasks, key=lambda t: t.position):
            tag_str = " ".join(task.tags) if task.tags else ""
            print(f"  {task.title} {tag_str}".rstrip())

        print(f"\nTotal: {len(tasks)} uncompleted tasks")

    asyncio.run(_main())
