"""C12 — On-Demand Handler.

Routes arbitrary user messages to the correct sub-handler based on detected
intent. All intents except GENERAL avoid an LLM call when possible.
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable

from config import Config
from context.assembler import AssembledContext
from handlers.base import BaseHandler, SendFn
from llm.client import LLMClient
from state.manager import StateManager
from utils.clock import Clock

if TYPE_CHECKING:
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

    if lower.startswith("add:") or lower.startswith("add :"):
        return Intent.ADD_TASK

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
    ) -> None:
        super().__init__(config, state_manager, clock)
        self._llm = llm_client
        self._build_context = context_builder
        self._followup = followup_handler
        self._joplin = joplin
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
        msg = f"Done. What's next, {self._config.user_name}?"
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
        await self._followup.schedule(response)
        await self._log_bot(response)

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
        """Match `done: <text>` to a Joplin task and mark it complete."""
        match = re.match(r"done\s*:\s*(.+)", text.strip(), re.IGNORECASE)
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
