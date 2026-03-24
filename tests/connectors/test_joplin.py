"""Tests for C3 — Joplin Connector."""

from unittest.mock import AsyncMock, patch

import pytest

from connectors.joplin import JoplinConnector


@pytest.fixture
def connector():
    return JoplinConnector(host="localhost", port=41184, token="test-token")


# ── Checklist parsing ─────────────────────────────────────────────────────────

def test_parse_checklist_empty_body(connector):
    assert connector._parse_checklist("") == []


def test_parse_checklist_finds_unchecked(connector):
    body = "- [ ] Buy milk\n- [ ] Call dentist"
    result = connector._parse_checklist(body)
    assert result == [(False, "Buy milk"), (False, "Call dentist")]


def test_parse_checklist_finds_checked(connector):
    body = "- [x] Done thing"
    result = connector._parse_checklist(body)
    assert result == [(True, "Done thing")]


def test_parse_checklist_mixed(connector):
    body = "- [ ] Task 1\n- [x] Task 2\n- [ ] Task 3"
    result = connector._parse_checklist(body)
    assert result == [(False, "Task 1"), (True, "Task 2"), (False, "Task 3")]


def test_parse_checklist_ignores_non_checklist_lines(connector):
    body = "Some intro text\n- [ ] Real task\nSome footer"
    result = connector._parse_checklist(body)
    assert len(result) == 1
    assert result[0] == (False, "Real task")


# ── Tag extraction ────────────────────────────────────────────────────────────

def test_extract_tags_none(connector):
    assert connector._extract_tags("Just a plain task") == []


def test_extract_tags_high(connector):
    assert "[high]" in connector._extract_tags("Fix this bug [high]")


def test_extract_tags_energy(connector):
    tags = connector._extract_tags("Read article [couch] [low-energy]")
    assert "[couch]" in tags
    assert "[low-energy]" in tags


def test_extract_tags_case_insensitive(connector):
    tags = connector._extract_tags("Task [HIGH]")
    assert "[high]" in tags  # normalised to lowercase


def test_extract_tags_all_known(connector):
    text = "[high] [low-energy] [couch] [easy]"
    tags = connector._extract_tags(text)
    assert set(tags) == {"[high]", "[low-energy]", "[couch]", "[easy]"}


# ── get_tasks: todo notes ─────────────────────────────────────────────────────

async def test_get_tasks_todo_note_included(connector):
    folders = [{"id": "f1", "title": "Work"}]
    notes = [_todo_note("n1", "Fix bug [high]", "f1", completed=False)]

    with patch.object(connector, "_get_all", new=AsyncMock(side_effect=[folders, notes])):
        tasks = await connector.get_tasks()

    assert len(tasks) == 1
    assert tasks[0].title == "Fix bug [high]"
    assert tasks[0].notebook == "Work"
    assert tasks[0].is_high_priority is True
    assert tasks[0].id == "n1"


async def test_get_tasks_completed_todo_excluded(connector):
    folders = [{"id": "f1", "title": "Work"}]
    notes = [_todo_note("n1", "Done task", "f1", completed=True)]

    with patch.object(connector, "_get_all", new=AsyncMock(side_effect=[folders, notes])):
        tasks = await connector.get_tasks()

    assert tasks == []


# ── get_tasks: checklist items ────────────────────────────────────────────────

async def test_get_tasks_checklist_unchecked_included(connector):
    folders = [{"id": "f1", "title": "Personal"}]
    notes = [_regular_note("n1", "My list", "f1", body="- [ ] Buy milk\n- [x] Done")]

    with patch.object(connector, "_get_all", new=AsyncMock(side_effect=[folders, notes])):
        tasks = await connector.get_tasks()

    assert len(tasks) == 1
    assert tasks[0].title == "Buy milk"
    assert tasks[0].id == "n1:0"
    assert tasks[0].notebook == "Personal"


async def test_get_tasks_checklist_all_checked_excluded(connector):
    folders = [{"id": "f1", "title": "Personal"}]
    notes = [_regular_note("n1", "Done list", "f1", body="- [x] A\n- [x] B")]

    with patch.object(connector, "_get_all", new=AsyncMock(side_effect=[folders, notes])):
        tasks = await connector.get_tasks()

    assert tasks == []


async def test_get_tasks_checklist_position_assigned(connector):
    folders = [{"id": "f1", "title": "Work"}]
    notes = [_regular_note("n1", "Sprint", "f1", body="- [ ] First\n- [ ] Second")]

    with patch.object(connector, "_get_all", new=AsyncMock(side_effect=[folders, notes])):
        tasks = await connector.get_tasks()

    assert len(tasks) == 2
    assert tasks[0].position == 0
    assert tasks[1].position == 1


async def test_get_tasks_unknown_notebook_fallback(connector):
    folders = []  # empty — note references a folder not in the list
    notes = [_todo_note("n1", "Orphan task", "missing_folder", completed=False)]

    with patch.object(connector, "_get_all", new=AsyncMock(side_effect=[folders, notes])):
        tasks = await connector.get_tasks()

    assert tasks[0].notebook == "Unknown"


# ── Failure handling ──────────────────────────────────────────────────────────

async def test_get_tasks_returns_empty_on_connection_error(connector):
    with patch.object(connector, "_get_all", new=AsyncMock(side_effect=Exception("connection refused"))):
        tasks = await connector.get_tasks()

    assert tasks == []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _todo_note(id: str, title: str, parent_id: str, *, completed: bool) -> dict:
    return {
        "id": id,
        "title": title,
        "body": "",
        "parent_id": parent_id,
        "type_": 2,
        "todo_completed": 1 if completed else 0,
        "order": 0,
        "updated_time": 1700000000000,
    }


def _regular_note(id: str, title: str, parent_id: str, *, body: str) -> dict:
    return {
        "id": id,
        "title": title,
        "body": body,
        "parent_id": parent_id,
        "type_": 1,
        "todo_completed": 0,
        "order": 0,
        "updated_time": 1700000000000,
    }
