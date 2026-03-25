"""Tests for C3 — Joplin Connector."""

from unittest.mock import AsyncMock, patch

import pytest

from connectors.joplin import JoplinConnector


_NOTEBOOK = "00_TODO"
_FOLDER_ID = "f-todo"


@pytest.fixture
def connector():
    return JoplinConnector(host="localhost", port=41184, token="test-token", notebook=_NOTEBOOK)


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


# ── Tag extraction — bracket syntax ──────────────────────────────────────────

def test_extract_tags_none(connector):
    assert connector._extract_tags("Just a plain task") == []


def test_extract_tags_bracket_high(connector):
    assert "[high]" in connector._extract_tags("Fix this bug [high]")


def test_extract_tags_bracket_low_energy(connector):
    tags = connector._extract_tags("Read article [couch] [low-energy]")
    assert "[low-energy]" in tags


def test_extract_tags_bracket_easy(connector):
    assert "[easy]" in connector._extract_tags("Quick task [easy]")


def test_extract_tags_case_insensitive_bracket(connector):
    assert "[high]" in connector._extract_tags("Task [HIGH]")


# ── Tag extraction — natural language ────────────────────────────────────────

def test_extract_tags_today(connector):
    assert "[today]" in connector._extract_tags("Send invoice today")


def test_extract_tags_by_eod(connector):
    assert "[today]" in connector._extract_tags("Finish report by EOD")


def test_extract_tags_by_eob(connector):
    assert "[today]" in connector._extract_tags("Review PR by EOB")


def test_extract_tags_do_it_today(connector):
    assert "[today]" in connector._extract_tags("Call the doctor - do it today")


def test_extract_tags_must_do_today(connector):
    assert "[today]" in connector._extract_tags("Must do today: pay electricity bill")


def test_extract_tags_urgent_slash_today(connector):
    tags = connector._extract_tags("Fix login bug urgent/today")
    assert "[today]" in tags


def test_extract_tags_urgent(connector):
    assert "[urgent]" in connector._extract_tags("Server is down - urgent")


def test_extract_tags_asap(connector):
    assert "[urgent]" in connector._extract_tags("Reply to client ASAP")


def test_extract_tags_this_week(connector):
    assert "[this-week]" in connector._extract_tags("Write tests this week")


def test_extract_tags_by_eow(connector):
    assert "[this-week]" in connector._extract_tags("Draft proposal by EOW")


def test_extract_tags_important(connector):
    assert "[high]" in connector._extract_tags("important: renew subscription")


def test_extract_tags_high_priority(connector):
    assert "[high]" in connector._extract_tags("high priority task")


def test_extract_tags_low_energy_natural(connector):
    assert "[low-energy]" in connector._extract_tags("Sort emails low energy")


def test_extract_tags_couch_natural(connector):
    assert "[low-energy]" in connector._extract_tags("Watch tutorial - couch")


def test_extract_tags_easy_natural(connector):
    assert "[easy]" in connector._extract_tags("Reply to newsletter - easy")


def test_extract_tags_quick_win(connector):
    assert "[easy]" in connector._extract_tags("quick win: update README")


def test_extract_tags_multiple(connector):
    tags = connector._extract_tags("Fix login bug urgent by EOD")
    assert "[urgent]" in tags
    assert "[today]" in tags


def test_extract_tags_no_duplicates(connector):
    tags = connector._extract_tags("today by EOD")
    assert tags.count("[today]") == 1


# ── get_tasks: notebook filtering ────────────────────────────────────────────

async def test_get_tasks_only_returns_todo_notebook(connector):
    folders = [
        {"id": _FOLDER_ID, "title": _NOTEBOOK},
        {"id": "f-other", "title": "Work"},
    ]
    notes = [
        _todo_note("n1", "Todo task", _FOLDER_ID, completed=False),
        _todo_note("n2", "Work task", "f-other", completed=False),
    ]

    with patch.object(connector, "_get_all", new=AsyncMock(side_effect=[folders, notes])):
        tasks = await connector.get_tasks()

    assert len(tasks) == 1
    assert tasks[0].title == "Todo task"


async def test_get_tasks_notebook_not_found_returns_empty(connector):
    folders = [{"id": "f-other", "title": "Work"}]
    notes = [_todo_note("n1", "Some task", "f-other", completed=False)]

    with patch.object(connector, "_get_all", new=AsyncMock(side_effect=[folders, notes])):
        tasks = await connector.get_tasks()

    assert tasks == []


# ── get_tasks: todo notes ─────────────────────────────────────────────────────

async def test_get_tasks_todo_note_included(connector):
    folders = [{"id": _FOLDER_ID, "title": _NOTEBOOK}]
    notes = [_todo_note("n1", "Fix bug [high]", _FOLDER_ID, completed=False)]

    with patch.object(connector, "_get_all", new=AsyncMock(side_effect=[folders, notes])):
        tasks = await connector.get_tasks()

    assert len(tasks) == 1
    assert tasks[0].title == "Fix bug [high]"
    assert tasks[0].notebook == _NOTEBOOK
    assert tasks[0].is_high_priority is True
    assert tasks[0].id == "n1"


async def test_get_tasks_completed_todo_excluded(connector):
    folders = [{"id": _FOLDER_ID, "title": _NOTEBOOK}]
    notes = [_todo_note("n1", "Done task", _FOLDER_ID, completed=True)]

    with patch.object(connector, "_get_all", new=AsyncMock(side_effect=[folders, notes])):
        tasks = await connector.get_tasks()

    assert tasks == []


# ── get_tasks: checklist items ────────────────────────────────────────────────

async def test_get_tasks_checklist_unchecked_included(connector):
    folders = [{"id": _FOLDER_ID, "title": _NOTEBOOK}]
    notes = [_regular_note("n1", "My list", _FOLDER_ID, body="- [ ] Buy milk\n- [x] Done")]

    with patch.object(connector, "_get_all", new=AsyncMock(side_effect=[folders, notes])):
        tasks = await connector.get_tasks()

    assert len(tasks) == 1
    assert tasks[0].title == "Buy milk"
    assert tasks[0].id == "n1:0"
    assert tasks[0].notebook == _NOTEBOOK


async def test_get_tasks_checklist_all_checked_excluded(connector):
    folders = [{"id": _FOLDER_ID, "title": _NOTEBOOK}]
    notes = [_regular_note("n1", "Done list", _FOLDER_ID, body="- [x] A\n- [x] B")]

    with patch.object(connector, "_get_all", new=AsyncMock(side_effect=[folders, notes])):
        tasks = await connector.get_tasks()

    assert tasks == []


async def test_get_tasks_checklist_position_assigned(connector):
    folders = [{"id": _FOLDER_ID, "title": _NOTEBOOK}]
    notes = [_regular_note("n1", "Sprint", _FOLDER_ID, body="- [ ] First\n- [ ] Second")]

    with patch.object(connector, "_get_all", new=AsyncMock(side_effect=[folders, notes])):
        tasks = await connector.get_tasks()

    assert len(tasks) == 2
    assert tasks[0].position == 0
    assert tasks[1].position == 1


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
        "is_todo": 1,
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
        "is_todo": 0,
        "todo_completed": 0,
        "order": 0,
        "updated_time": 1700000000000,
    }


def _make_todo_task(note_id="n1", title="Fix bug") -> "Task":
    from connectors.models import Task
    return Task(
        id=note_id, note_id=note_id, title=title,
        notebook=_NOTEBOOK, notebook_id=_FOLDER_ID,
        tags=[], is_high_priority=False, position=0, updated_time=0,
        is_checklist_item=False, checklist_item_text=None,
    )


def _make_checklist_task(note_id="n1", title="Buy milk") -> "Task":
    from connectors.models import Task
    return Task(
        id=f"{note_id}:0", note_id=note_id, title=title,
        notebook=_NOTEBOOK, notebook_id=_FOLDER_ID,
        tags=[], is_high_priority=False, position=0, updated_time=0,
        is_checklist_item=True, checklist_item_text=title,
    )


# ── mark_done: standalone todo ────────────────────────────────────────────────

async def test_mark_done_todo_calls_put(connector):
    task = _make_todo_task()
    with patch.object(connector, "_put", new=AsyncMock(return_value={})) as mock_put:
        result = await connector.mark_done(task)
    assert result is True
    mock_put.assert_called_once()
    path, data = mock_put.call_args[0][1], mock_put.call_args[0][2]
    assert path == f"/notes/{task.note_id}"
    assert "todo_completed" in data
    assert data["todo_completed"] > 0


async def test_mark_done_todo_returns_false_on_error(connector):
    task = _make_todo_task()
    with patch.object(connector, "_put", new=AsyncMock(side_effect=Exception("network error"))):
        result = await connector.mark_done(task)
    assert result is False


# ── mark_done: checklist item ─────────────────────────────────────────────────

async def test_mark_done_checklist_patches_body(connector):
    task = _make_checklist_task(note_id="n1", title="Buy milk")
    note_body = "- [ ] Buy milk\n- [ ] Call dentist"
    expected_body = "- [x] Buy milk\n- [ ] Call dentist"

    with patch.object(connector, "_get", new=AsyncMock(return_value={"id": "n1", "body": note_body})), \
         patch.object(connector, "_put", new=AsyncMock(return_value={})) as mock_put:
        result = await connector.mark_done(task)

    assert result is True
    put_data = mock_put.call_args[0][2]
    assert put_data["body"] == expected_body


async def test_mark_done_checklist_item_not_found_returns_false(connector):
    task = _make_checklist_task(note_id="n1", title="Missing item")
    note_body = "- [ ] Buy milk\n- [ ] Call dentist"

    with patch.object(connector, "_get", new=AsyncMock(return_value={"id": "n1", "body": note_body})), \
         patch.object(connector, "_put", new=AsyncMock(return_value={})):
        result = await connector.mark_done(task)

    assert result is False


# ── create_task ───────────────────────────────────────────────────────────────

async def test_create_task_posts_to_notes(connector):
    connector._todo_folder_id = _FOLDER_ID
    with patch.object(connector, "_post", new=AsyncMock(return_value={"id": "new-note-id"})) as mock_post:
        result = await connector.create_task("Write tests")
    assert result == "new-note-id"
    post_data = mock_post.call_args[0][2]
    assert post_data["title"] == "Write tests"
    assert post_data["is_todo"] == 1
    assert post_data["parent_id"] == _FOLDER_ID


async def test_create_task_returns_none_on_error(connector):
    connector._todo_folder_id = _FOLDER_ID
    with patch.object(connector, "_post", new=AsyncMock(side_effect=Exception("timeout"))):
        result = await connector.create_task("Write tests")
    assert result is None


async def test_create_task_fetches_folder_if_not_cached(connector):
    assert connector._todo_folder_id is None
    folders = [{"id": _FOLDER_ID, "title": _NOTEBOOK}]
    with patch.object(connector, "_get_all", new=AsyncMock(return_value=folders)), \
         patch.object(connector, "_post", new=AsyncMock(return_value={"id": "x"})):
        result = await connector.create_task("New task")
    assert result == "x"
    assert connector._todo_folder_id == _FOLDER_ID


# ── get_tasks: new Task fields ────────────────────────────────────────────────

async def test_get_tasks_todo_note_id_equals_id(connector):
    folders = [{"id": _FOLDER_ID, "title": _NOTEBOOK}]
    notes = [_todo_note("n1", "Fix bug", _FOLDER_ID, completed=False)]
    with patch.object(connector, "_get_all", new=AsyncMock(side_effect=[folders, notes])):
        tasks = await connector.get_tasks()
    assert tasks[0].note_id == "n1"
    assert tasks[0].is_checklist_item is False
    assert tasks[0].checklist_item_text is None


async def test_get_tasks_checklist_item_fields(connector):
    folders = [{"id": _FOLDER_ID, "title": _NOTEBOOK}]
    notes = [_regular_note("n1", "My list", _FOLDER_ID, body="- [ ] Buy milk")]
    with patch.object(connector, "_get_all", new=AsyncMock(side_effect=[folders, notes])):
        tasks = await connector.get_tasks()
    assert tasks[0].note_id == "n1"
    assert tasks[0].is_checklist_item is True
    assert tasks[0].checklist_item_text == "Buy milk"
