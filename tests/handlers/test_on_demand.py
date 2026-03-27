"""Tests for C12 — On-Demand Handler."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from context.assembler import AssembledContext, Mode
from handlers.on_demand import Intent, OnDemandHandler, detect_intent


# ── detect_intent ─────────────────────────────────────────────────────────────

def test_intent_off_today():
    assert detect_intent("off today") == Intent.OFF_TODAY

def test_intent_off_today_case_insensitive():
    assert detect_intent("Off Today") == Intent.OFF_TODAY

def test_intent_full_silence():
    assert detect_intent("off today full silence") == Intent.OFF_TODAY
    # full_silence flag is parsed inside the handler, not a separate intent

def test_intent_finished_done():
    assert detect_intent("done") == Intent.FINISHED

def test_intent_finished_i_finished():
    assert detect_intent("I finished the report") == Intent.FINISHED

def test_intent_finished_done_with():
    assert detect_intent("done with the slides") == Intent.FINISHED

def test_intent_stuck():
    assert detect_intent("I'm stuck") == Intent.STUCK

def test_intent_stuck_struggling():
    assert detect_intent("I'm struggling with this") == Intent.STUCK

def test_intent_skip():
    assert detect_intent("skip") == Intent.SKIP

def test_intent_add_task_colon():
    assert detect_intent("add: buy milk") == Intent.ADD_TASK

def test_intent_use_opus():
    assert detect_intent("<USE_OPUS>") == Intent.USE_OPUS

def test_intent_use_opus_lowercase():
    assert detect_intent("<use_opus>") == Intent.USE_OPUS

def test_intent_general_fallback():
    assert detect_intent("how does the calendar look?") == Intent.GENERAL

def test_intent_trigger_morning():
    assert detect_intent("!morning") == Intent.TRIGGER

def test_intent_trigger_evening():
    assert detect_intent("!evening") == Intent.TRIGGER

def test_intent_trigger_any_exclamation():
    assert detect_intent("!bedtime") == Intent.TRIGGER


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_daily(**overrides):
    base = {
        "date": "2026-03-25", "morning_complete": True,
        "morning_questions_asked": [], "declared_energy": None,
        "off_today": False, "off_today_full_silence": False,
        "task_queue": [], "opus_session_active": False,
        "opus_session_messages": 0, "last_suggestion": None, "last_suggestion_ts": None,
        "last_suggested_task_id": None, "commitment_minutes": None,
    }
    base.update(overrides)
    return base


def _make_context():
    return AssembledContext(
        mode=Mode.WORK, energy="medium",
        now=datetime(2026, 3, 25, 10, 0, tzinfo=ZoneInfo("Europe/Berlin")),
        is_weekend=False, has_prior_history=True,
        tasks=[], events=[], free_windows=[], recent_interactions=[],
        daily_state=_make_daily(), text="=== context ===",
    )


@pytest.fixture
def config():
    cfg = MagicMock()
    cfg.user_name = "Gabriell"
    cfg.timezone = "Europe/Berlin"
    return cfg


@pytest.fixture
def state_manager():
    sm = MagicMock()
    sm.get_daily = AsyncMock(return_value=_make_daily())
    sm.update_daily = AsyncMock()
    sm.append_interaction = AsyncMock()
    return sm


@pytest.fixture
def clock():
    c = MagicMock()
    c.now.return_value = datetime(2026, 3, 25, 10, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    return c


@pytest.fixture
def llm_client():
    llm = MagicMock()
    llm.send = AsyncMock(return_value="Here is a suggestion.")
    return llm


@pytest.fixture
def context_builder():
    return AsyncMock(return_value=_make_context())


@pytest.fixture
def followup_handler():
    fh = MagicMock()
    fh.schedule = AsyncMock()
    fh.cancel = MagicMock()
    return fh


@pytest.fixture
def handler(config, state_manager, clock, llm_client, context_builder, followup_handler):
    return OnDemandHandler(
        config=config,
        state_manager=state_manager,
        clock=clock,
        llm_client=llm_client,
        context_builder=context_builder,
        followup_handler=followup_handler,
    )


# ── off today ─────────────────────────────────────────────────────────────────

async def test_handle_off_today_sets_flag(handler, state_manager):
    await handler.handle("off today", AsyncMock())
    state_manager.update_daily.assert_called_once()
    kwargs = state_manager.update_daily.call_args[1]
    assert kwargs["off_today"] is True
    assert kwargs["off_today_full_silence"] is False


async def test_handle_off_today_full_silence(handler, state_manager):
    await handler.handle("off today full silence", AsyncMock())
    kwargs = state_manager.update_daily.call_args[1]
    assert kwargs["off_today_full_silence"] is True


async def test_handle_off_today_sends_ack(handler):
    send_fn = AsyncMock()
    await handler.handle("off today", send_fn)
    send_fn.assert_called_once()
    assert "quiet" in send_fn.call_args[0][0].lower() or "got it" in send_fn.call_args[0][0].lower()


# ── finished ──────────────────────────────────────────────────────────────────

async def test_handle_finished_sends_ack(handler):
    send_fn = AsyncMock()
    await handler.handle("I finished the report", send_fn)
    send_fn.assert_called_once()
    # Should push for one more action, not just acknowledge
    assert "good" in send_fn.call_args[0][0].lower() or "moving" in send_fn.call_args[0][0].lower()


async def test_handle_finished_cancels_followup(handler, followup_handler):
    await handler.handle("done", AsyncMock())
    followup_handler.cancel.assert_called_once()


async def test_handle_finished_logs_interaction(handler, state_manager):
    await handler.handle("done", AsyncMock())
    state_manager.append_interaction.assert_called()


# ── stuck ─────────────────────────────────────────────────────────────────────

async def test_handle_stuck_calls_llm(handler, llm_client):
    await handler.handle("I'm stuck", AsyncMock())
    llm_client.send.assert_called_once()


async def test_handle_stuck_shows_timer_picker(handler, followup_handler):
    """After stuck response, EVA should show a timer picker instead of auto-scheduling."""
    send_fn = AsyncMock()
    await handler.handle("I'm stuck", send_fn)
    # send_fn called twice: LLM response + timer picker prompt
    assert send_fn.call_count == 2
    # Second call should have a view argument (TimerPickerView)
    second_call_kwargs = send_fn.call_args_list[1][1]
    assert "view" in second_call_kwargs


async def test_handle_stuck_sends_response(handler):
    send_fn = AsyncMock()
    await handler.handle("I'm stuck", send_fn)
    # First call is the LLM suggestion
    assert send_fn.call_args_list[0][0][0] == "Here is a suggestion."


# ── skip ─────────────────────────────────────────────────────────────────────

async def test_handle_skip_sends_message(handler):
    send_fn = AsyncMock()
    await handler.handle("skip", send_fn)
    send_fn.assert_called_once()


async def test_handle_skip_does_not_call_llm(handler, llm_client):
    await handler.handle("skip", AsyncMock())
    llm_client.send.assert_not_called()


# ── add task (fallback: no joplin) ───────────────────────────────────────────

async def test_handle_add_task_fallback_appends_to_queue(handler, state_manager):
    """Without a Joplin connector, add: falls back to local queue."""
    state_manager.get_daily = AsyncMock(return_value=_make_daily(task_queue=[]))
    await handler.handle("add: buy milk", AsyncMock())
    state_manager.update_daily.assert_called_once()
    queue = state_manager.update_daily.call_args[1]["task_queue"]
    assert len(queue) == 1
    assert "buy milk" in queue[0]["title"]


async def test_handle_add_task_fallback_sends_confirmation(handler, state_manager):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(task_queue=[]))
    send_fn = AsyncMock()
    await handler.handle("add: buy milk", send_fn)
    send_fn.assert_called_once()
    assert "buy milk" in send_fn.call_args[0][0].lower()


# ── add task (with joplin) ────────────────────────────────────────────────────

@pytest.fixture
def joplin():
    j = MagicMock()
    j.create_task = AsyncMock(return_value="new-note-id")
    j.get_tasks = AsyncMock(return_value=[])
    j.mark_done = AsyncMock(return_value=True)
    return j


@pytest.fixture
def handler_with_joplin(config, state_manager, clock, llm_client, context_builder, followup_handler, joplin):
    return OnDemandHandler(
        config=config, state_manager=state_manager, clock=clock,
        llm_client=llm_client, context_builder=context_builder,
        followup_handler=followup_handler, joplin=joplin,
    )


async def test_handle_add_task_calls_joplin(handler_with_joplin, joplin):
    send_fn = AsyncMock()
    await handler_with_joplin.handle("add: buy milk", send_fn)
    joplin.create_task.assert_called_once_with("buy milk")
    send_fn.assert_called_once()
    assert "buy milk" in send_fn.call_args[0][0].lower()


async def test_handle_add_task_joplin_failure_falls_back(handler_with_joplin, joplin, state_manager):
    joplin.create_task = AsyncMock(return_value=None)
    state_manager.get_daily = AsyncMock(return_value=_make_daily(task_queue=[]))
    send_fn = AsyncMock()
    await handler_with_joplin.handle("add: buy milk", send_fn)
    state_manager.update_daily.assert_called_once()  # fell back to local queue


# ── use opus ─────────────────────────────────────────────────────────────────

async def test_handle_use_opus_activates_session(handler, state_manager):
    await handler.handle("<USE_OPUS>", AsyncMock())
    state_manager.update_daily.assert_called_once_with(
        opus_session_active=True, opus_session_messages=0
    )


async def test_handle_use_opus_sends_confirmation(handler):
    send_fn = AsyncMock()
    await handler.handle("<USE_OPUS>", send_fn)
    send_fn.assert_called_once()
    assert "opus" in send_fn.call_args[0][0].lower()


# ── general ──────────────────────────────────────────────────────────────────

async def test_handle_general_calls_llm(handler, llm_client):
    await handler.handle("what should I work on?", AsyncMock())
    llm_client.send.assert_called_once()


# ── trigger ───────────────────────────────────────────────────────────────────

@pytest.fixture
def scheduler():
    s = MagicMock()
    s.trigger = AsyncMock(return_value=True)
    return s


async def test_handle_trigger_calls_scheduler(handler, scheduler):
    handler.set_scheduler(scheduler)
    send_fn = AsyncMock()
    await handler.handle("!evening", send_fn)
    scheduler.trigger.assert_called_once_with("evening", send_fn)


async def test_handle_trigger_sends_ack(handler, scheduler):
    handler.set_scheduler(scheduler)
    send_fn = AsyncMock()
    await handler.handle("!morning", send_fn)
    send_fn.assert_called_once()


async def test_handle_trigger_unknown_name(handler, scheduler):
    handler.set_scheduler(scheduler)
    send_fn = AsyncMock()
    await handler.handle("!foobar", send_fn)
    scheduler.trigger.assert_not_called()
    assert "unknown" in send_fn.call_args[0][0].lower()


async def test_handle_trigger_no_scheduler(handler):
    send_fn = AsyncMock()
    await handler.handle("!evening", send_fn)
    send_fn.assert_called_once()
    assert "not ready" in send_fn.call_args[0][0].lower()


async def test_handle_general_sends_llm_response(handler):
    send_fn = AsyncMock()
    await handler.handle("what should I work on?", send_fn)
    send_fn.assert_called_once_with("Here is a suggestion.")


# ── detect_intent: DONE_TASK ──────────────────────────────────────────────────

def test_intent_done_task_colon():
    assert detect_intent("done: fix login bug") == Intent.DONE_TASK

def test_intent_done_task_colon_spaced():
    assert detect_intent("done : send the email") == Intent.DONE_TASK

def test_intent_done_without_colon_is_finished():
    # Without colon, "done <text>" is now FINISHED — task marking requires colon or buttons.
    assert detect_intent("done Get rid of Substack account") == Intent.FINISHED

def test_intent_done_text_without_colon_is_finished():
    assert detect_intent("done fix login bug") == Intent.FINISHED

def test_intent_done_plain_is_finished():
    assert detect_intent("done") == Intent.FINISHED

def test_intent_done_with_is_finished():
    assert detect_intent("done with the slides") == Intent.FINISHED

def test_intent_done_already_is_finished():
    assert detect_intent("done already") == Intent.FINISHED

def test_intent_done_for_today_is_finished():
    assert detect_intent("done for today") == Intent.FINISHED

def test_intent_done_it_is_finished():
    assert detect_intent("done it") == Intent.FINISHED

def test_intent_done_i_finished_is_finished():
    assert detect_intent("I finished the report") == Intent.FINISHED


# ── done: <task> handler ──────────────────────────────────────────────────────

def _make_task(id="t1", title="Fix bug"):
    from connectors.models import Task
    return Task(
        id=id, note_id=id, title=title, notebook="00_TODO", notebook_id="f1",
        tags=[], is_high_priority=False, position=0, updated_time=0,
        is_checklist_item=False, checklist_item_text=None,
    )


async def test_handle_done_task_marks_joplin_done(handler_with_joplin, joplin, llm_client):
    task = _make_task(id="t1", title="Fix login bug")
    joplin.get_tasks = AsyncMock(return_value=[task])
    llm_client.send = AsyncMock(return_value="t1")
    send_fn = AsyncMock()
    await handler_with_joplin.handle("done: fix login bug", send_fn)
    joplin.mark_done.assert_called_once_with(task)
    assert "fix login bug" in send_fn.call_args[0][0].lower()


async def test_handle_done_task_no_match_sends_error(handler_with_joplin, joplin, llm_client):
    task = _make_task(id="t1", title="Unrelated task")
    joplin.get_tasks = AsyncMock(return_value=[task])
    llm_client.send = AsyncMock(return_value="NO_MATCH")
    send_fn = AsyncMock()
    await handler_with_joplin.handle("done: something else", send_fn)
    joplin.mark_done.assert_not_called()
    assert send_fn.call_args[0][0]  # some error message was sent


async def test_handle_done_task_no_joplin_sends_error(handler):
    send_fn = AsyncMock()
    await handler.handle("done: fix login bug", send_fn)
    send_fn.assert_called_once()
    assert "not available" in send_fn.call_args[0][0].lower()


# ── finished: auto-complete last suggested task ───────────────────────────────

async def test_handle_finished_auto_marks_done(handler_with_joplin, joplin, state_manager):
    task = _make_task(id="t1", title="Fix login bug")
    joplin.get_tasks = AsyncMock(return_value=[task])
    state_manager.get_daily = AsyncMock(return_value=_make_daily(last_suggested_task_id="t1"))
    send_fn = AsyncMock()
    await handler_with_joplin.handle("done", send_fn)
    joplin.mark_done.assert_called_once_with(task)
    assert "fix login bug" in send_fn.call_args[0][0].lower()


async def test_handle_finished_no_task_id_skips_joplin(handler_with_joplin, joplin, state_manager):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(last_suggested_task_id=None))
    await handler_with_joplin.handle("done", AsyncMock())
    joplin.mark_done.assert_not_called()


# ── detect_intent: ADD_EVENT ──────────────────────────────────────────────────

def test_intent_schedule_colon():
    assert detect_intent("schedule: dentist tomorrow at 14:00") == Intent.ADD_EVENT

def test_intent_add_event_colon():
    assert detect_intent("add event: team lunch on Friday") == Intent.ADD_EVENT

def test_intent_add_event_uppercase():
    assert detect_intent("Schedule: call with client") == Intent.ADD_EVENT

def test_intent_add_task_not_confused_with_add_event():
    assert detect_intent("add: buy milk") == Intent.ADD_TASK


# ── _handle_add_event ─────────────────────────────────────────────────────────

_VALID_EVENT_JSON = '{"title": "Dentist", "date": "2026-03-26", "start_time": "14:00", "duration_min": 60, "calendar_id": "primary"}'


@pytest.fixture
def calendar():
    cal = MagicMock()
    cal.create_event = AsyncMock(return_value="new-event-id")
    return cal


@pytest.fixture
def handler_with_calendar(config, state_manager, clock, llm_client, context_builder, followup_handler, calendar):
    return OnDemandHandler(
        config=config, state_manager=state_manager, clock=clock,
        llm_client=llm_client, context_builder=context_builder,
        followup_handler=followup_handler, calendar=calendar,
    )


async def test_handle_add_event_calls_create_event(handler_with_calendar, calendar, llm_client):
    llm_client.send = AsyncMock(return_value=_VALID_EVENT_JSON)
    send_fn = AsyncMock()
    await handler_with_calendar.handle("schedule: dentist tomorrow at 14:00", send_fn)
    calendar.create_event.assert_called_once()
    args = calendar.create_event.call_args[0]
    assert args[0] == "Dentist"
    assert args[1].hour == 14
    assert args[2].hour == 15  # 60 min later


async def test_handle_add_event_sends_confirmation(handler_with_calendar, calendar, llm_client):
    llm_client.send = AsyncMock(return_value=_VALID_EVENT_JSON)
    send_fn = AsyncMock()
    await handler_with_calendar.handle("schedule: dentist", send_fn)
    send_fn.assert_called_once()
    assert "dentist" in send_fn.call_args[0][0].lower()


async def test_handle_add_event_no_calendar_sends_error(handler):
    send_fn = AsyncMock()
    await handler.handle("schedule: dentist at 14:00", send_fn)
    send_fn.assert_called_once()
    assert "not available" in send_fn.call_args[0][0].lower()


async def test_handle_add_event_bad_json_from_llm(handler_with_calendar, llm_client):
    llm_client.send = AsyncMock(return_value="this is not json")
    send_fn = AsyncMock()
    await handler_with_calendar.handle("schedule: dentist", send_fn)
    send_fn.assert_called_once()
    assert "parse" in send_fn.call_args[0][0].lower() or "couldn't" in send_fn.call_args[0][0].lower()


async def test_handle_add_event_missing_fields(handler_with_calendar, llm_client):
    llm_client.send = AsyncMock(return_value='{"title": "Dentist", "date": null, "start_time": null}')
    send_fn = AsyncMock()
    await handler_with_calendar.handle("schedule: dentist", send_fn)
    send_fn.assert_called_once()
    assert "missing" in send_fn.call_args[0][0].lower()


async def test_handle_add_event_add_event_prefix(handler_with_calendar, calendar, llm_client):
    llm_client.send = AsyncMock(return_value=_VALID_EVENT_JSON)
    send_fn = AsyncMock()
    await handler_with_calendar.handle("add event: dentist at 14:00", send_fn)
    calendar.create_event.assert_called_once()


async def test_handle_add_event_markdown_json_stripped(handler_with_calendar, calendar, llm_client):
    llm_client.send = AsyncMock(return_value="```json\n" + _VALID_EVENT_JSON + "\n```")
    send_fn = AsyncMock()
    await handler_with_calendar.handle("schedule: dentist", send_fn)
    calendar.create_event.assert_called_once()


# ── detect_intent: COMMIT ─────────────────────────────────────────────────────

def test_detect_intent_commit_i_need():
    assert detect_intent("I need 17 minutes to finish the report") == Intent.COMMIT

def test_detect_intent_commit_give_me():
    assert detect_intent("give me 20 min") == Intent.COMMIT

def test_detect_intent_commit_starts_with_number():
    assert detect_intent("17 min") == Intent.COMMIT

def test_detect_intent_commit_with_commit_prefix():
    assert detect_intent("commit: 25 mins for the Schilling prep") == Intent.COMMIT

def test_detect_intent_commit_not_triggered_by_done():
    # "done" should take priority
    assert detect_intent("done: the report") != Intent.COMMIT


def test_detect_intent_commit_not_triggered_by_long_message():
    # Long multi-part messages mentioning "I need N min" incidentally should NOT be COMMIT
    long_msg = (
        "ok, I have about 30 mins now; I need to:\n"
        "send out invite to Schilling with the location\n"
        "I need 5 mins to send out the invite to Schilling"
    )
    assert detect_intent(long_msg) == Intent.GENERAL


def test_detect_intent_commit_extracts_mins_not_first_number():
    # "I need 5 mins to send the invite" — must extract 5, not some other number
    assert detect_intent("I need 5 mins to send the invite") == Intent.COMMIT


# ── _handle_commit ────────────────────────────────────────────────────────────

async def test_handle_commit_schedules_timer(handler, state_manager, followup_handler):
    """User commits to a specific duration — followup should be scheduled."""
    send_fn = AsyncMock()
    with patch.object(followup_handler, "schedule", new=AsyncMock()) as mock_schedule:
        await handler.handle("I need 17 minutes to finish the report", send_fn)
    mock_schedule.assert_called_once()
    args = mock_schedule.call_args
    assert args[1]["minutes"] == 17


async def test_handle_commit_extracts_task(handler, state_manager, followup_handler):
    send_fn = AsyncMock()
    with patch.object(followup_handler, "schedule", new=AsyncMock()) as mock_schedule:
        await handler.handle("I need 20 minutes to finish the Schilling prep", send_fn)
    call_args = mock_schedule.call_args
    task = call_args[0][0]  # first positional arg is the task/suggestion
    assert "Schilling" in task


async def test_handle_commit_falls_back_to_last_suggestion(handler, state_manager, followup_handler):
    state_manager.get_daily = AsyncMock(return_value=_make_daily(
        last_suggestion="Write the proposal"
    ))
    send_fn = AsyncMock()
    with patch.object(followup_handler, "schedule", new=AsyncMock()) as mock_schedule:
        await handler.handle("17 min", send_fn)
    call_args = mock_schedule.call_args
    task = call_args[0][0]
    assert "proposal" in task.lower()


async def test_handle_commit_extracts_task_with_mins(handler, state_manager, followup_handler):
    """'I need 5 mins to send the invite' — must extract 5 (not some other number) and the task."""
    send_fn = AsyncMock()
    with patch.object(followup_handler, "schedule", new=AsyncMock()) as mock_schedule:
        await handler.handle("I need 5 mins to send the Schilling invite", send_fn)
    args = mock_schedule.call_args
    assert args[1]["minutes"] == 5
    task = args[0][0]
    assert "Schilling" in task


async def test_handle_commit_rejects_out_of_range(handler, followup_handler):
    send_fn = AsyncMock()
    await handler.handle("I need 500 minutes", send_fn)
    msg = send_fn.call_args[0][0]
    assert "240" in msg or "between" in msg.lower()
