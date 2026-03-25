"""C8 — Morning Routine Handler.

Stateful multi-turn morning interview. Question progress is persisted in
state.json so the conversation survives a bot restart mid-routine.

Question sequence:
  1. energy   — declares energy level for the day
  2. goal     — primary goal for the day
  3. blockers — anything in the way

After all three, an LLM summary is generated and morning_complete is set.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from config import Config
from context.assembler import AssembledContext
from handlers.base import BaseHandler, SendFn
from llm.client import LLMClient
from state.manager import StateManager
from utils.clock import Clock

log = logging.getLogger(__name__)

# Questions in order — key matches state field name for tracking
_QUESTIONS = [
    ("energy",   "How's your energy this morning? (high / medium / low)"),
    ("goal",     "What's the one thing you most want to accomplish today?"),
    ("blockers", "Anything on your mind that might get in the way today?"),
]
_QUESTION_KEYS = [k for k, _ in _QUESTIONS]
_QUESTION_MAP  = dict(_QUESTIONS)


def _parse_energy(text: str) -> str:
    """Infer declared energy from free-text answer."""
    t = text.lower()
    if any(w in t for w in ("low", "tired", "exhausted", "rough", "bad", "awful", "drained")):
        return "low"
    if any(w in t for w in ("high", "great", "excellent", "energetic", "amazing", "fantastic")):
        return "high"
    return "medium"


class MorningRoutineHandler(BaseHandler):
    """C8 — Stateful multi-turn morning interview.

    Args:
        config: Bot configuration.
        state_manager: Persists question progress across restarts.
        clock: Clock instance.
        llm_client: For generating the closing summary.
        context_builder: Async callable returning a fresh AssembledContext.
    """

    def __init__(
        self,
        config: Config,
        state_manager: StateManager,
        clock: Clock,
        llm_client: LLMClient,
        context_builder: Callable[[], Awaitable[AssembledContext]],
    ) -> None:
        super().__init__(config, state_manager, clock)
        self._llm = llm_client
        self._build_context = context_builder

    # ── Scheduled entry points ────────────────────────────────────────────────

    async def fire(self, send_fn: SendFn) -> None:
        """Send the first morning question. Called by the scheduler at morning_routine time."""
        daily = await self._state.get_daily()
        if daily["off_today"]:
            return
        if daily["morning_complete"]:
            return
        if daily["morning_questions_asked"]:
            return  # already in progress — waiting for the user's next reply

        greeting = (
            f"Good morning, {self._config.user_name}! 🌅\n"
            f"{_QUESTION_MAP['energy']}"
        )
        await send_fn(greeting)
        await self._state.update_daily(morning_questions_asked=["energy"])
        await self._log_bot(greeting)

    async def fire_retry(self, send_fn: SendFn) -> None:
        """Send a gentle nudge if no response to the morning routine yet."""
        daily = await self._state.get_daily()
        if daily["morning_complete"] or daily["off_today"]:
            return

        msg = (
            f"{self._config.user_name} — morning check-in is waiting. "
            "Every minute you delay is momentum you won't get back today."
        )
        await send_fn(msg)
        await self._log_bot(msg)

    # ── Interactive entry point ───────────────────────────────────────────────

    async def handle_response(self, text: str, send_fn: SendFn) -> bool:
        """Process a user reply during the morning routine.

        Returns True when the routine completes, False otherwise.
        Callers should check is_active() before routing here.
        """
        daily = await self._state.get_daily()
        asked = daily["morning_questions_asked"]

        if not asked or daily["morning_complete"]:
            return False

        await self._log_user(text)
        last_asked = asked[-1]

        if last_asked == "energy":
            energy = _parse_energy(text)
            next_q = _QUESTION_MAP["goal"]
            await self._state.update_daily(
                declared_energy=energy,
                morning_questions_asked=asked + ["goal"],
            )
            await send_fn(next_q)
            await self._log_bot(next_q)
            return False

        if last_asked == "goal":
            next_q = _QUESTION_MAP["blockers"]
            await self._state.update_daily(morning_questions_asked=asked + ["blockers"])
            await send_fn(next_q)
            await self._log_bot(next_q)
            return False

        if last_asked == "blockers":
            ctx = await self._build_context()
            trigger = (
                f"{self._config.user_name} has completed the morning check-in. "
                "Generate a sharp 2–3 sentence day-plan based on what they shared: "
                "the priority, the first action, and what stands between them and a good day. "
                "No fluff. Make it sound like a plan that will actually happen."
            )
            response = await self._llm.send(ctx, trigger)
            await self._state.update_daily(morning_complete=True)
            await send_fn(response)
            await self._log_bot(response)
            return True

        return False

    # ── State query ───────────────────────────────────────────────────────────

    async def is_active(self) -> bool:
        """True when the routine has started but not yet completed."""
        daily = await self._state.get_daily()
        return bool(daily["morning_questions_asked"]) and not daily["morning_complete"]
