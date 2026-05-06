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
    from handlers.reminder import ReminderHandler
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
    REMINDER = "reminder"
    COMMIT = "commit"
    CEILING = "ceiling"
    ADHD_ON = "adhd_on"
    ADHD_OFF = "adhd_off"
    USE_OPUS = "use_opus"
    TRIGGER = "trigger"
    GENERAL = "general"


# Natural-language "<task> done" patterns. Examples:
#   "Make photo of X - done, take it off the list"
#   "Miriam must talk to the insurance\ndone"
#   "buy milk, done"
#   "Write to Friedemann: done"
#   "Set up email-done"
# Requires a clear punctuation separator (newline / dash / colon / comma) with
# any amount of optional whitespace around it, so we don't catch "I'm done with X"
# mid-sentence (which has only spaces and is handled by FINISHED).
_DONE_TRAILING_RE = re.compile(
    r'^(.+?)'                         # task text (lazy)
    r'(?:\n+\s*|\s*[-–—:,]\s*)'      # separator: newline / dash / colon / comma
    r'done\b',                        # word "done"
    re.IGNORECASE | re.DOTALL,
)


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
    "nudge":        "nudge",
    "help":         "help",
}

_HELP_TEXT = """**EVA — Quick Reference**

**Commands** (prefix with `!`):
> `!morning` — trigger morning check-in
> `!kickoff` — trigger day kick-off briefing
> `!midday` — trigger midday check-in
> `!evening` — trigger evening check-in
> `!eod` / `!end of day` — trigger end-of-day review
> `!bedtime` — trigger bedtime reminder
> `!retry` — re-send morning check-in nudge
> `!help` — show this reference

**Keywords** (type directly):
> `off today` — suppress all proactive messages for the day
> `off today full silence` — suppress everything including bedtime
> `done: <task>` — mark a task as done in Joplin
> `add: <task>` — add a new task to Joplin inbox
> `schedule: <event>` — add a Google Calendar event
> `remind me at 14:30 about X` / `reminder 21:30: X` — set a timed reminder
> `remind me tomorrow at 09:45: X` — reminder for tomorrow
> `remind me on friday at 13:00: X` — reminder for a specific day
> `I need 20 min` / `give me 15 min` / `check back in 15 min` / `remind me in 30 min` — set a timer
> `done` / `finished` — mark current task done, cancel timer
> `stuck` / `struggling` — get unstuck help + timer picker
> `skip` — skip current suggestion
> `<use_opus>` — switch to Opus model for this session

**Task tags** (in Joplin — auto-detected from task text):
> `[today]` / `eod` / `asap` — must be done today
> `[urgent]` — drop everything
> `[this-week]` / `eow` — sometime this week
> `[high]` / `important` — high priority, not time-bound
> `[low-energy]` / `[couch]` — can do when tired / from the couch
> `[easy]` / `quick` — quick win

**Everything else** → goes to the LLM as a general message.
"""


# ── Pure intent detection (module-level for easy testing) ─────────────────────

def detect_intent(text: str) -> Intent:
    """Classify a user message into an Intent without side effects."""
    lower = text.lower().strip()

    if lower.startswith("!"):
        return Intent.TRIGGER

    if re.search(r"<use_opus>", lower):
        return Intent.USE_OPUS

    # ADHD mode toggles. Match exact `/adhd` and `/normal` (with optional trailing
    # whitespace/punctuation) so they don't collide with other slash patterns.
    if re.match(r"/adhd\b", lower):
        return Intent.ADHD_ON
    if re.match(r"/normal\b", lower):
        return Intent.ADHD_OFF

    if lower.startswith("off today"):
        return Intent.OFF_TODAY

    # "done: <task>" — explicit colon required for task completion via text.
    # All other task-done actions go through buttons (FollowupView, CheckinView).
    if re.match(r"done\s*:", lower):
        return Intent.DONE_TASK

    if re.match(r"(schedule|add\s+event)\s*:", lower):
        return Intent.ADD_EVENT

    if lower.startswith("add:") or lower.startswith("add :"):
        return Intent.ADD_TASK

    # `ceiling: <minutes>` — hard-stop timer. Distinct from `commit` (soft check-in)
    # and from `<task> done` (write-back). Refuses extensions when fired.
    if re.match(r"ceiling\s*:\s*\d+", lower):
        return Intent.CEILING

    # Timed reminder: "remind me at 14:30 about X", "reminder 21:30: X",
    # "remind me tomorrow at 09:45: X", "remind me on friday at 13:00: X"
    # Must come BEFORE COMMIT check since "remind me in 30 min" is COMMIT.
    if re.match(
        r'remind(?:er|(?:\s+me))?\s+'
        r'(?:(?:tomorrow|on\s+\w+)\s+)?'
        r'(?:at\s+)?'
        r'\d{1,2}[:.]\d{2}',
        lower,
    ):
        return Intent.REMINDER

    # Commitment timer: "I need 5 min", "give me 20 mins", "15 min", "commit 10 min",
    # "check back in 15 min", "remind me in 30 min", "I need another 10 min", "timer 20 min"
    if (
        re.search(r'\b(?:i need|give me)(?:\s+\w+)?\s+\d+\s*min(?:utes?|s)?\b', lower)
        or re.search(r'\bcommit[:\s]+\d+\s*min(?:utes?|s)?\b', lower)
        or re.search(r'\b(?:check back|remind me|timer)\s+(?:in\s+)?\d+\s*min(?:utes?|s)?\b', lower)
        or re.match(r'\d+\s*min(?:utes?|s)?\b', lower)
    ):
        return Intent.COMMIT

    # Natural-language "<task> done" — task text followed by separator + "done".
    # Must come before FINISHED so "X\ndone" routes to write-back, not to the
    # bare-done auto-complete path.
    if _DONE_TRAILING_RE.match(text.strip()):
        return Intent.DONE_TASK

    # FINISHED / STUCK: only at the start of the message, not mid-sentence.
    if re.match(r"(i'?m\s+|i\s+)?(done|finished|completed|done with)\b", lower):
        return Intent.FINISHED
    if re.match(r"(i'?m\s+|i\s+)?(stuck|struggling)\b", lower):
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
        reminder_handler: "ReminderHandler | None" = None,
    ) -> None:
        super().__init__(config, state_manager, clock)
        self._llm = llm_client
        self._build_context = context_builder
        self._followup = followup_handler
        self._joplin = joplin
        self._calendar = calendar
        self._reminder = reminder_handler
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
        elif intent == Intent.REMINDER:
            await self._handle_reminder(text, send_fn)
        elif intent == Intent.COMMIT:
            await self._handle_commit(text, send_fn)
        elif intent == Intent.CEILING:
            await self._handle_ceiling(text, send_fn)
        elif intent == Intent.ADHD_ON:
            await self._handle_adhd_on(send_fn)
        elif intent == Intent.ADHD_OFF:
            await self._handle_adhd_off(send_fn)
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
        if job == "help":
            await send_fn(_HELP_TEXT)
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
        # Auto-activate ADHD mode if not already active.
        # Stuck is the canonical overwhelm trigger and benefits from the
        # response-shape compression (3-sentence cap, option-deletion).
        daily = await self._state.get_daily()
        if not daily.get("adhd_mode_active"):
            await self._activate_adhd("stuck")
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
        # Post-ceiling guard: if the ADHD ceiling has already fired, refuse
        # extensions until the user explicitly types `/normal` or the day rolls.
        if await self._is_post_ceiling():
            await self._refuse_post_ceiling_extension(text, send_fn)
            return

        # Extract minutes from various patterns:
        # "i need 17 min", "give me 20 min", "commit: 25 min",
        # "check back in 15 min", "remind me in 30 min", "timer 20 min",
        # "I need another 10 min", or bare "17 min"
        match = re.search(
            r'(?:i need|give me)(?:\s+\w+)?\s+(\d+)\s*(?:min(?:utes?|s)?)',
            text, re.IGNORECASE,
        )
        if not match:
            match = re.search(
                r'commit[:\s]+(\d+)\s*(?:min(?:utes?|s)?)',
                text, re.IGNORECASE,
            )
        if not match:
            match = re.search(
                r'(?:check back|remind me|timer)\s+(?:in\s+)?(\d+)\s*(?:min(?:utes?|s)?)',
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

        # Extract task: text after the matched time spec.
        # Handles "to X", "for X", ": X", or "— X" separators after the duration.
        after_match = text[match.end():]
        task_after = re.match(
            r'\s*[:\-—]+\s*(.+)|'       # ": escalate avis" or "— do X"
            r'\s+(?:to|for)\s+(.+)',     # "to finalize" or "for the task"
            after_match, re.IGNORECASE | re.DOTALL,
        )
        if task_after:
            task = (task_after.group(1) or task_after.group(2) or "").strip().split('\n')[0].strip()
        else:
            # Check for task specified BEFORE the duration: "remind me in 30 min" preceded by context
            task = ""

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
        """Match a Joplin task and mark it complete.

        Accepts both forms:
          - `done: <task>` (explicit prefix)
          - `<task> done [trailing]` (natural language: dash, newline, or comma separator)
        """
        stripped = text.strip()
        match = re.match(r"done\s*:\s*(.+)", stripped, re.IGNORECASE | re.DOTALL)
        if match:
            task_text = match.group(1).strip()
        else:
            match = _DONE_TRAILING_RE.match(stripped)
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

    async def _handle_reminder(self, text: str, send_fn: SendFn) -> None:
        """Parse a timed reminder and schedule it via ReminderHandler."""
        if self._reminder is None:
            await send_fn("Reminder system not available.")
            return

        from zoneinfo import ZoneInfo
        from handlers.reminder import parse_reminder

        tz = ZoneInfo(self._config.timezone)
        result = parse_reminder(text, self._clock.now(), tz)
        if result is None:
            # Couldn't parse — fall through to general LLM handler
            await self._handle_general(text, send_fn)
            return

        reminder_text, fire_at = result
        await self._reminder.schedule(reminder_text, fire_at)
        time_label = fire_at.strftime("%H:%M")
        date_label = fire_at.strftime("%A %d %b")
        today = self._clock.now().date()
        if fire_at.date() == today:
            msg = f"Reminder set for **{time_label}** today: {reminder_text}"
        else:
            msg = f"Reminder set for **{date_label} {time_label}**: {reminder_text}"
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
        # Late-night ADHD override: after end_of_day_review (default 22:30),
        # free-form messages get a sleep redirect instead of an LLM call.
        # Write-back shortcuts (done:, add:, schedule:, ceiling:, /adhd, /normal)
        # already routed elsewhere by detect_intent — they keep working.
        if await self._is_late_night_adhd():
            now = self._clock.now()
            msg = (
                f"It's {now.strftime('%H:%M')}. Sleep is the highest-leverage "
                f"action available. Goodnight, {self._config.user_name}."
            )
            await send_fn(msg)
            await self._log_user(text)
            await self._log_bot(msg)
            return

        ctx = await self._build_context()
        response = await self._llm.send(ctx, text)
        await send_fn(response)
        await self._log_user(text)
        await self._log_bot(response)

    # ── ADHD mode handlers ────────────────────────────────────────────────────

    async def _handle_adhd_on(self, send_fn: SendFn) -> None:
        """Manual `/adhd` shortcut — flip the flag and start the ceiling."""
        daily = await self._state.get_daily()
        if daily.get("adhd_mode_active"):
            ceiling_iso = daily.get("adhd_block_ceiling_at")
            label = ""
            if ceiling_iso:
                from datetime import datetime
                try:
                    label = f" Ceiling at {datetime.fromisoformat(ceiling_iso).strftime('%H:%M')}."
                except ValueError:
                    pass
            msg = f"Already in ADHD mode.{label}"
            await send_fn(msg)
            await self._log_bot(msg)
            return

        ceiling_minutes = await self._activate_adhd("manual")
        from datetime import timedelta
        at_time = (self._clock.now() + timedelta(minutes=ceiling_minutes)).strftime("%H:%M")
        msg = (
            f"ADHD mode on — ceiling at {at_time} ({ceiling_minutes} min). "
            f"One action at a time. `/normal` to exit."
        )
        await send_fn(msg)
        await self._log_bot(msg)

    async def _handle_adhd_off(self, send_fn: SendFn) -> None:
        """Manual `/normal` shortcut — clear the flag and cancel the ceiling."""
        daily = await self._state.get_daily()
        if not daily.get("adhd_mode_active"):
            msg = "Not in ADHD mode."
            await send_fn(msg)
            await self._log_bot(msg)
            return

        # Close out the open activation log entry.
        activations = list(daily.get("adhd_activations") or [])
        if activations and activations[-1].get("ended_at") is None:
            activations[-1]["ended_at"] = self._clock.now().isoformat()
            activations[-1]["end_reason"] = "manual"
        await self._state.update_daily(
            adhd_mode_active=False,
            adhd_block_started_at=None,
            adhd_block_ceiling_at=None,
            adhd_activations=activations,
        )
        self._followup.cancel_ceiling()

        msg = "ADHD mode off."
        await send_fn(msg)
        await self._log_bot(msg)

    async def _handle_ceiling(self, text: str, send_fn: SendFn) -> None:
        """Standalone `ceiling: <minutes>` shortcut. Hard-stop, refuses extensions."""
        match = re.match(r"ceiling\s*:\s*(\d+)\s*(?:min(?:utes?|s)?)?\s*(.*)$", text.strip(), re.IGNORECASE)
        if not match:
            await send_fn("Usage: `ceiling: <minutes>` (e.g. `ceiling: 45`).")
            return
        minutes = int(match.group(1))
        if not 1 <= minutes <= 240:
            await send_fn("Ceiling must be between 1 and 240 minutes.")
            return
        task = match.group(2).strip().lstrip(":-—,").strip() or ""

        await self._followup.schedule_ceiling(minutes, task=task)
        from datetime import timedelta
        at_time = (self._clock.now() + timedelta(minutes=minutes)).strftime("%H:%M")
        task_label = f" for {task}" if task else ""
        msg = (
            f"Ceiling set — {minutes} min{task_label}. Hard stop at {at_time}. "
            f"No extensions."
        )
        await send_fn(msg)
        await self._log_user(text)
        await self._log_bot(msg)

    # ── ADHD helpers ──────────────────────────────────────────────────────────

    async def _activate_adhd(self, trigger: str) -> int:
        """Set the ADHD flag, append an activation log entry, schedule the ceiling.

        Returns the ceiling duration in minutes (so callers can craft the reply).
        """
        now = self._clock.now()
        ceiling_minutes = self._config.adhd_default_ceiling_min
        from datetime import timedelta
        ceiling_at = now + timedelta(minutes=ceiling_minutes)

        daily = await self._state.get_daily()
        activations = list(daily.get("adhd_activations") or [])
        activations.append({
            "started_at": now.isoformat(),
            "trigger": trigger,
            "ceiling_at": ceiling_at.isoformat(),
            "ended_at": None,
            "end_reason": None,
        })
        await self._state.update_daily(
            adhd_mode_active=True,
            adhd_activations=activations,
        )
        # schedule_ceiling writes adhd_block_started_at / adhd_block_ceiling_at.
        await self._followup.schedule_ceiling(ceiling_minutes)
        log.info("ADHD mode activated (trigger=%s, ceiling=%d min)", trigger, ceiling_minutes)
        return ceiling_minutes

    async def _is_post_ceiling(self) -> bool:
        """True if the ADHD ceiling has fired and not yet been cleared."""
        daily = await self._state.get_daily()
        if not daily.get("adhd_mode_active"):
            return False
        ceiling_iso = daily.get("adhd_block_ceiling_at")
        if not ceiling_iso:
            return False
        from datetime import datetime
        try:
            return self._clock.now() > datetime.fromisoformat(ceiling_iso)
        except ValueError:
            return False

    async def _refuse_post_ceiling_extension(self, text: str, send_fn: SendFn) -> None:
        msg = "No. Ceiling exists because you don't stop without it. Close the laptop."
        await send_fn(msg)
        await self._log_user(text)
        await self._log_bot(msg)

    async def _is_late_night_adhd(self) -> bool:
        """True if ADHD mode is active AND we're past end_of_day_review."""
        daily = await self._state.get_daily()
        if not daily.get("adhd_mode_active"):
            return False
        from datetime import time
        h, m = self._config.end_of_day_review.split(":")
        threshold = time(int(h), int(m))
        now_t = self._clock.now().time()
        # Late-night window: from end_of_day_review until midnight rollover.
        return now_t >= threshold
