"""C12 — On-Demand Handler.

Routes arbitrary user messages to the correct sub-handler based on detected
intent. All intents except GENERAL avoid an LLM call when possible.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable

from config import Config
from context.assembler import AssembledContext
from handlers.base import BaseHandler, SendFn
from llm.client import LLMClient
from state.manager import StateManager
from utils.clock import Clock

from handlers.followup import TimerPickerView

if TYPE_CHECKING:
    from connectors.calendar import CalendarConnector
    from connectors.joplin import JoplinConnector
    from handlers.followup import FollowupHandler
    from scheduler import Scheduler

log = logging.getLogger(__name__)


class Intent(str, Enum):
    OFF_TODAY = "off_today"
    FINISHED = "finished"
    DONE_TASK = "done_task"
    STUCK = "stuck"
    SKIP = "skip"
    ADD_TASK = "add_task"
    ADD_EVENT = "add_event"
    COMMIT = "commit"
    USE_OPUS = "use_opus"
    TRIGGER = "trigger"
    GENERAL = "general"


_TRIGGER_ALIASES: dict[str, str] = {
    "morning":      "morning",
    "retry":        "retry",
    "morning retry":"retry",
    "kickoff":      "kickoff",
    "kick off":     "kickoff",
    "midday":       "midday",
    "evening":      "evening",
    "eod":          "eod",
    "end of day":   "eod",
    "bedtime":      "bedtime",
}


# ── Pure intent detection (module-level for easy testing) ─────────────────────

def detect_intent(text: str) -> Intent:
    """Classify a user message into an Intent without side effects."""
    lower = text.lower().strip()

    if lower.startswith("!"):
        return Intent.TRIGGER

    if re.search(r"<use_opus>", lower):
        return Intent.USE_OPUS

    if lower.startswith("off today"):
        return Intent.OFF_TODAY

    if re.match(r"done\s*:", lower):
        return Intent.DONE_TASK

    # "done <task text>" without colon — but not "done with/it/already/for/today/now/everything"
    if re.match(r"done\s+(?!(with|it|already|for|today|now|everything)\b)\S", lower):
        return Intent.DONE_TASK

    if re.match(r"(schedule|add\s+event)\s*:", lower):
        return Intent.ADD_EVENT

    if lower.startswith("add:") or lower.startswith("add :"):
        return Intent.ADD_TASK

    # Commitment timer: only short, focused timer messages.
    # "I need 17 mins to X", "give me 20 min", "17 min", "commit: 25 mins"
    # Must NOT match long multi-part messages where "I need N min" is incidental.
    # Require the pattern near the start (first 60 chars) or the message to be short.
    if len(lower) < 80 and (
        re.search(r'\b(?:i need|give me|commit)[:\s]+\d+\s*min', lower)
        or re.match(r'\d+\s*min', lower)
    ):
        return Intent.COMMIT

    if re.search(r"\b(done|finished|completed|i finished|done with)\b", lower):
        return Intent.FINISHED

    if re.search(r"\b(stuck|struggling)\b", lower):
        return Intent.STUCK

    if re.search(r"^skip\b", lower):
        return Intent.SKIP

    return Intent.GENERAL


# ── Handler ───────────────────────────────────────────────────────────────────

class OnDemandHandler(BaseHandler):
    """C12 — Routes on-demand user messages by intent.

    Args:
        config: Bot configuration.
        state_manager: For state reads/writes and interaction logging.
        clock: Clock instance.
        llm_client: Used for STUCK, GENERAL, and DONE_TASK intents.
        context_builder: Async callable returning a fresh AssembledContext.
        followup_handler: C13 instance for scheduling/cancelling follow-ups.
        joplin: JoplinConnector for task write-back (ADD_TASK, DONE_TASK).
    """

    def __init__(
        self,
        config: Config,
        state_manager: StateManager,
        clock: Clock,
        llm_client: LLMClient,
        context_builder: Callable[[], Awaitable[AssembledContext]],
        followup_handler: FollowupHandler,
        joplin: "JoplinConnector | None" = None,
        calendar: "CalendarConnector | None" = None,
    ) -> None:
        super().__init__(config, state_manager, clock)
        self._llm = llm_client
        self._build_context = context_builder
        self._followup = followup_handler
        self._joplin = joplin
        self._calendar = calendar
        self._scheduler: Scheduler | None = None

    def set_scheduler(self, scheduler: Scheduler) -> None:
        """Inject the Scheduler after creation (avoids circular dependency)."""
        self._scheduler = scheduler

    async def handle(self, text: str, send_fn: SendFn) -> None:
        """Dispatch text to the correct sub-handler based on intent."""
        intent = detect_intent(text)
        log.debug("OnDemandHandler: intent=%s text=%r", intent, text[:80])

        if intent == Intent.TRIGGER:
            await self._handle_trigger(text, send_fn)
        elif intent == Intent.OFF_TODAY:
            await self._handle_off_today(text, send_fn)
        elif intent == Intent.FINISHED:
            await self._handle_finished(text, send_fn)
        elif intent == Intent.DONE_TASK:
            await self._handle_done_task(text, send_fn)
        elif intent == Intent.STUCK:
            await self._handle_stuck(send_fn)
        elif intent == Intent.SKIP:
            await self._handle_skip(send_fn)
        elif intent == Intent.ADD_TASK:
            await self._handle_add_task(text, send_fn)
        elif intent == Intent.ADD_EVENT:
            await self._handle_add_event(text, send_fn)
        elif intent == Intent.COMMIT:
            await self._handle_commit(text, send_fn)
        elif intent == Intent.USE_OPUS:
            await self._handle_use_opus(send_fn)
        else:
            await self._handle_general(text, send_fn)

    # ── Intent sub-handlers ───────────────────────────────────────────────────

    async def _handle_trigger(self, text: str, send_fn: SendFn) -> None:
        name = text.strip().lstrip("!").strip().lower()
        job = _TRIGGER_ALIASES.get(name)
        if job is None:
            available = ", ".join(f"`!{k}`" for k in _TRIGGER_ALIASES if k == _TRIGGER_ALIASES[k])
            await send_fn(f"Unknown trigger `!{name}`. Available: {available}")
            return
        if self._scheduler is None:
            await send_fn("Scheduler not ready yet — try again in a moment.")
            return
        await send_fn(f"Triggering `{job}`...")
        await self._scheduler.trigger(job, send_fn)

    async def _handle_off_today(self, text: str, send_fn: SendFn) -> None:
        full_silence = "full silence" in text.lower()
        await self._state.update_daily(
            off_today=True,
            off_today_full_silence=full_silence,
        )
        if full_silence:
            msg = "Got it — staying quiet for the rest of the day. Take care of yourself."
        else:
            msg = (
                f"Got it, {self._config.user_name}. I'll keep quiet today. "
                "Bedtime reminder still on — reply 'off today full silence' to mute that too."
            )
        await send_fn(msg)
        await self._log_user(text)
        await self._log_bot(msg)

    async def _handle_finished(self, text: str, send_fn: SendFn) -> None:
        self._followup.cancel()

        # Auto-complete last suggested Joplin task if we have one recorded
        daily = await self._state.get_daily()
        task_id = daily.get("last_suggested_task_id")
        if task_id and self._joplin is not None:
            tasks = await self._joplin.get_tasks()
            task = next((t for t in tasks if t.id == task_id), None)
            if task:
                await self._joplin.mark_done(task)
                await self._state.update_daily(last_suggested_task_id=None)
                msg = f"Done. Marked **{task.title}** as done in Joplin. What's next, {self._config.user_name}?"
                await send_fn(msg)
                await self._log_user(text)
                await self._log_bot(msg)
                return

        hour = self._clock.now().hour
        if hour >= 20:
            msg = (
                f"Good. One more before you close out, {self._config.user_name} — "
                "what's it going to be? The version of you that wins acts now."
            )
        else:
            msg = (
                f"Good. Keep moving, {self._config.user_name}. "
                "What's next on the list? The version of you that succeeds acts immediately."
            )
        await send_fn(msg)
        await self._log_user(text)
        await self._log_bot(msg)

    async def _handle_stuck(self, send_fn: SendFn) -> None:
        ctx = await self._build_context()
        trigger = (
            f"{self._config.user_name} says they're stuck. "
            "Name what is most likely blocking them based on context. "
            "Give the single smallest physical action that breaks the freeze. "
            "Direct and concrete — under 80 words. No comfort, no padding."
        )
        response = await self._llm.send(ctx, trigger)
        await send_fn(response)
        await self._log_bot(response)
        view = TimerPickerView(handler=self._followup, suggestion=response)
        await send_fn("How long do you need? Set your commitment:", view=view)

    async def _handle_commit(self, text: str, send_fn: SendFn) -> None:
        """Parse a time commitment and schedule a check-back timer."""
        # Use the same patterns as detect_intent so we extract from the right match:
        # "i need 17 min...", "give me 20 min...", "commit: 25 min...", or "17 min..."
        match = re.search(
            r'(?:i need|give me|commit)[:\s]+(\d+)\s*(?:min(?:utes?|s)?)',
            text, re.IGNORECASE,
        )
        if not match:
            # Bare "17 min" at start of message
            match = re.match(r'(\d+)\s*(?:min(?:utes?|s)?)', text.strip(), re.IGNORECASE)
        if not match:
            await send_fn("I didn't catch the duration. Try: \"I need 20 minutes to finish X\"")
            return

        minutes = int(match.group(1))
        if not 1 <= minutes <= 240:
            await send_fn("Timer must be between 1 and 240 minutes.")
            return

        # Extract task: text after the matched time spec, following "to" or "for"
        after_match = text[match.end():]
        task_after = re.match(r'\s+(?:to|for)\s+(.+)', after_match, re.IGNORECASE | re.DOTALL)
        task = task_after.group(1).strip().split('\n')[0].strip() if task_after else ""

        # Fall back to last_suggestion if no explicit task given
        if not task:
            daily = await self._state.get_daily()
            task = daily.get("last_suggestion") or "the task"

        await self._followup.schedule(task, minutes=minutes)
        at_time = (self._clock.now() + timedelta(minutes=minutes)).strftime("%H:%M")
        msg = f"Committed — {minutes} minutes for: {task}. I'll check back at {at_time}."
        await send_fn(msg)
        await self._log_user(text)
        await self._log_bot(msg)

    async def _handle_skip(self, send_fn: SendFn) -> None:
        msg = f"Skipped. That task is still on the list, {self._config.user_name}."
        await send_fn(msg)
        await self._log_bot(msg)

    async def _handle_add_task(self, text: str, send_fn: SendFn) -> None:
        match = re.match(r"add\s*:\s*(.+)", text.strip(), re.IGNORECASE)
        task_text = match.group(1).strip() if match else text.strip()

        if self._joplin is not None:
            note_id = await self._joplin.create_task(task_text)
            if note_id:
                msg = f"Added to Joplin: **{task_text}**"
                await send_fn(msg)
                await self._log_user(text)
                await self._log_bot(msg)
                return
            log.warning("Joplin create_task failed — falling back to local queue")

        # Fallback: local state queue (Joplin unavailable)
        daily = await self._state.get_daily()
        queue = list(daily.get("task_queue") or [])
        ts = self._clock.now().isoformat()
        queue.append({"id": f"local_{ts}", "title": task_text, "added_at": ts})
        await self._state.update_daily(task_queue=queue)
        msg = f"Added to local queue (Joplin unavailable): **{task_text}**"
        await send_fn(msg)
        await self._log_user(text)
        await self._log_bot(msg)

    async def _handle_done_task(self, text: str, send_fn: SendFn) -> None:
        """Match `done: <text>` or `done <text>` to a Joplin task and mark it complete."""
        match = re.match(r"done\s*:?\s*(.+)", text.strip(), re.IGNORECASE)
        task_text = match.group(1).strip() if match else ""

        if not task_text:
            await send_fn("Usage: `done: <task description>`")
            return

        if self._joplin is None:
            await send_fn("Joplin not available — can't mark tasks done right now.")
            return

        tasks = await self._joplin.get_tasks()
        if not tasks:
            await send_fn("No tasks found in Joplin — nothing to mark done.")
            return

        task_list = "\n".join(f"- id={t.id} | {t.title}" for t in tasks)
        ctx = await self._build_context()
        extraction_prompt = (
            f"{self._config.user_name} says they finished: \"{task_text}\".\n"
            f"Task list:\n{task_list}\n\n"
            "Reply with ONLY the task id that best matches what they finished. "
            "If nothing matches, reply with exactly: NO_MATCH"
        )
        raw = await self._llm.send(ctx, extraction_prompt)
        matched_id = raw.strip().strip('"').strip("'")

        task = next((t for t in tasks if t.id == matched_id), None)
        if task is None:
            await send_fn(
                f"Couldn't find a Joplin task matching \"{task_text}\". "
                "Check your task list or be more specific."
            )
            return

        success = await self._joplin.mark_done(task)
        if success:
            msg = f"Marked done in Joplin: **{task.title}**"
        else:
            msg = f"Joplin write failed — couldn't mark **{task.title}** as done."
        await send_fn(msg)
        await self._log_user(text)
        await self._log_bot(msg)

    async def _handle_add_event(self, text: str, send_fn: SendFn) -> None:
        """Extract event details via LLM and create a Google Calendar event."""
        if self._calendar is None:
            await send_fn("Calendar not available — can't add events right now.")
            return

        raw = re.sub(r"^(schedule|add\s+event)\s*:\s*", "", text.strip(), flags=re.IGNORECASE)
        if not raw:
            await send_fn("Usage: `schedule: <description>`  e.g. `schedule: dentist tomorrow at 14:00 for 1 hour`")
            return

        now = self._clock.now()
        now_str = now.strftime("%Y-%m-%d %H:%M (%A)")
        ctx = await self._build_context()
        extraction_prompt = (
            f"Current date/time: {now_str}. Timezone: {self._config.timezone}.\n"
            f"{self._config.user_name} wants to add a calendar event: \"{raw}\"\n\n"
            "Extract the event details and reply with ONLY a JSON object (no markdown) with these keys:\n"
            "  title       (string — event name)\n"
            "  date        (string — YYYY-MM-DD)\n"
            "  start_time  (string — HH:MM, 24h)\n"
            "  duration_min (integer — minutes, default 60 if not specified)\n"
            "  calendar_id (string — always \"primary\" unless user specifies another)\n"
            "If you cannot determine a required field, set it to null."
        )
        raw_json = await self._llm.send(ctx, extraction_prompt)

        # Strip potential markdown code fences
        clean = re.sub(r"^```[a-z]*\n?|```$", "", raw_json.strip(), flags=re.MULTILINE).strip()
        try:
            fields = json.loads(clean)
        except json.JSONDecodeError:
            await send_fn("Couldn't parse the event details. Try: `schedule: <title> on <date> at <time> for <duration>`")
            return

        title = fields.get("title")
        date_str = fields.get("date")
        start_str = fields.get("start_time")
        duration_min = fields.get("duration_min") or 60
        calendar_id = fields.get("calendar_id") or "primary"

        if not title or not date_str or not start_str:
            missing = [f for f, v in [("title", title), ("date", date_str), ("start time", start_str)] if not v]
            await send_fn(f"Missing: {', '.join(missing)}. Try: `schedule: <title> on <date> at <time>`")
            return

        from datetime import datetime
        from zoneinfo import ZoneInfo
        try:
            tz = ZoneInfo(self._config.timezone)
            start_dt = datetime.fromisoformat(f"{date_str}T{start_str}:00").replace(tzinfo=tz)
            end_dt = start_dt + timedelta(minutes=int(duration_min))
        except (ValueError, TypeError) as exc:
            await send_fn(f"Couldn't parse date/time ({exc}). Use YYYY-MM-DD and HH:MM.")
            return

        try:
            event_id = await self._calendar.create_event(title, start_dt, end_dt, calendar_id)
        except Exception as exc:
            log.error("Calendar create_event failed: %s", exc)
            await send_fn(f"Calendar write failed: {exc}")
            return

        date_label = start_dt.strftime("%A %d %b")
        time_label = f"{start_dt.strftime('%H:%M')}–{end_dt.strftime('%H:%M')}"
        msg = f"Added to calendar: **{title}** — {date_label} {time_label}"
        await send_fn(msg)
        await self._log_user(text)
        await self._log_bot(msg)

    async def _handle_use_opus(self, send_fn: SendFn) -> None:
        await self._state.update_daily(
            opus_session_active=True,
            opus_session_messages=0,
        )
        msg = (
            "Switching to Opus for this session. "
            "I'll use claude-opus-4-6 until the session ends or the message limit is reached."
        )
        await send_fn(msg)
        await self._log_bot(msg)

    async def _handle_general(self, text: str, send_fn: SendFn) -> None:
        ctx = await self._build_context()
        response = await self._llm.send(ctx, text)
        await send_fn(response)
        await self._log_user(text)
        await self._log_bot(response)
