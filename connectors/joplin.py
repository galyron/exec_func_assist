"""C3 — Joplin Connector.

Fetches tasks from the Joplin REST API (Web Clipper / CLI server).
Read-only in Phases 1 and 2.

Handles two task sources:
  - Standalone todo notes (type_=2, not todo_completed)
  - Unchecked checklist items within regular note bodies

Inline tags extracted from both title and body:
  [high], [low-energy], [couch], [easy]

Standalone usage (for verification):
    docker compose exec bot python -m connectors.joplin
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import aiohttp

from connectors.models import Task

log = logging.getLogger(__name__)

_CHECKLIST_RE = re.compile(r"^- \[( |x)\] (.+)$", re.MULTILINE)
_INLINE_TAG_RE = re.compile(r"\[(high|low-energy|couch|easy)\]", re.IGNORECASE)
_NOTE_FIELDS = "id,title,body,parent_id,type_,todo_completed,order,updated_time"
_FOLDER_FIELDS = "id,title"


class JoplinConnector:
    """Async client for the Joplin Data REST API.

    Args:
        host: Joplin API host (Docker service name "joplin" or IP).
        port: Joplin API port (default 41184).
        token: Joplin API token (set in .env as JOPLIN_API_TOKEN).
    """

    def __init__(self, host: str, port: int, token: str) -> None:
        self._base = f"http://{host}:{port}"
        self._token = token

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_tasks(self) -> list[Task]:
        """Return all uncompleted tasks across all notebooks.

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
        tasks: list[Task] = []

        for note in notes:
            notebook_id = note.get("parent_id", "")
            notebook = folder_map.get(notebook_id, "Unknown")

            if note.get("type_") == 2:
                # Standalone todo note
                if not note.get("todo_completed"):
                    tasks.append(self._todo_to_task(note, notebook, notebook_id))
            else:
                # Regular note — extract unchecked checklist items
                body = note.get("body") or ""
                for pos, (checked, text) in enumerate(self._parse_checklist(body)):
                    if not checked:
                        tags = self._extract_tags(text)
                        tasks.append(Task(
                            id=f"{note['id']}:{pos}",
                            title=text.strip(),
                            notebook=notebook,
                            notebook_id=notebook_id,
                            tags=tags,
                            is_high_priority="[high]" in tags,
                            position=pos,
                            updated_time=note.get("updated_time", 0),
                        ))

        return tasks

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
            title=title,
            notebook=notebook,
            notebook_id=notebook_id,
            tags=tags,
            is_high_priority="[high]" in tags,
            position=int(note.get("order") or 0),
            updated_time=note.get("updated_time", 0),
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
        """Return normalised inline tags found in text."""
        return [f"[{m.group(1).lower()}]" for m in _INLINE_TAG_RE.finditer(text)]


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
        )

        if not await connector.ping():
            print("ERROR: Joplin API not reachable. Is the joplin container running?")
            return

        tasks = await connector.get_tasks()
        if not tasks:
            print("No uncompleted tasks found.")
            return

        current_notebook = None
        for task in sorted(tasks, key=lambda t: (t.notebook, t.position)):
            if task.notebook != current_notebook:
                current_notebook = task.notebook
                print(f"\n[{current_notebook}]")
            tag_str = " ".join(task.tags) if task.tags else ""
            print(f"  {task.title} {tag_str}".rstrip())

        print(f"\nTotal: {len(tasks)} uncompleted tasks")

    asyncio.run(_main())
