"""C2 — State Manager.

Async read/write for all JSON state files. Writes are atomic:
data is written to a .tmp file then renamed, so a killed process
cannot produce a corrupt state file.

All methods are safe to call concurrently from a single asyncio event
loop (the bot is single-process, single-loop).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiofiles

from state.models import (
    BotState,
    DailyState,
    Interaction,
    InteractionLog,
    MemoryStore,
    PreviousDailyState,
    default_bot_state,
    default_daily_state,
    default_interaction_log,
    default_memory_store,
)
from utils.clock import Clock

log = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"


class StateManager:
    """Manages all persistent bot state.

    Args:
        data_dir: Directory where JSON state files live. Created if absent.
        clock: Clock instance used for timestamping.
    """

    def __init__(self, data_dir: Path = _DEFAULT_DATA_DIR, *, clock: Clock) -> None:
        self._dir = data_dir
        self._clock = clock
        self._state_path = data_dir / "state.json"
        self._interactions_path = data_dir / "interactions.json"
        self._memory_path = data_dir / "memory.json"

    # ── Initialisation ────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create data directory and state files if they do not exist.

        Must be called once at startup before any other method.
        """
        self._dir.mkdir(parents=True, exist_ok=True)

        today = self._today_str()

        if not self._state_path.exists():
            log.info("First run detected — creating state files.")
            await self._write_json(self._state_path, default_bot_state(today))
        else:
            # Roll over daily state if the date has changed.
            state = await self.load_state()
            if state["daily"]["date"] != today:
                log.info("New day detected — rolling over daily state.")
                await self._rollover_daily(state, today)

        if not self._interactions_path.exists():
            await self._write_json(self._interactions_path, default_interaction_log())

        if not self._memory_path.exists():
            await self._write_json(self._memory_path, default_memory_store())

    # ── State ─────────────────────────────────────────────────────────────────

    async def load_state(self) -> BotState:
        return await self._read_json(self._state_path)

    async def save_state(self, state: BotState) -> None:
        await self._write_json(self._state_path, state)

    async def is_first_run(self) -> bool:
        """True until the bot has completed its first successful startup."""
        state = await self.load_state()
        return not state["first_run_completed"]

    async def mark_first_run_complete(self) -> None:
        state = await self.load_state()
        state["first_run_completed"] = True
        await self.save_state(state)

    async def get_daily(self) -> DailyState:
        state = await self.load_state()
        return state["daily"]

    async def update_daily(self, **kwargs) -> None:
        """Update one or more fields in the daily state."""
        state = await self.load_state()
        for key, value in kwargs.items():
            if key not in DailyState.__annotations__:
                raise KeyError(f"Unknown daily state field: {key!r}")
            state["daily"][key] = value  # type: ignore[literal-required]
        await self.save_state(state)

    async def has_previous_daily(self) -> bool:
        state = await self.load_state()
        return state.get("previous_daily") is not None

    # ── Interaction log ───────────────────────────────────────────────────────

    async def append_interaction(self, interaction: Interaction) -> None:
        log_data: InteractionLog = await self._read_json(self._interactions_path)
        log_data["interactions"].append(interaction)
        await self._write_json(self._interactions_path, log_data)

    async def get_recent_interactions(self, n: int = 5) -> list[Interaction]:
        log_data: InteractionLog = await self._read_json(self._interactions_path)
        return log_data["interactions"][-n:]

    async def get_today_interactions(self) -> list[Interaction]:
        today = self._today_str()
        log_data: InteractionLog = await self._read_json(self._interactions_path)
        return [i for i in log_data["interactions"] if i["timestamp"].startswith(today)]

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _rollover_daily(self, state: BotState, new_date: str) -> None:
        """Archive current daily state to previous_daily and reset for today."""
        old = state["daily"]
        state["previous_daily"] = PreviousDailyState(
            date=old["date"],
            declared_energy=old["declared_energy"],
            task_queue=old["task_queue"],
            morning_complete=old["morning_complete"],
        )
        state["daily"] = default_daily_state(new_date)
        await self.save_state(state)

    def _today_str(self) -> str:
        return self._clock.now().strftime("%Y-%m-%d")

    def _now_iso(self) -> str:
        return self._clock.now().isoformat()

    # ── I/O ───────────────────────────────────────────────────────────────────

    async def _read_json(self, path: Path) -> dict:
        async with aiofiles.open(path) as f:
            return json.loads(await f.read())

    async def _write_json(self, path: Path, data: dict) -> None:
        """Atomic write: write to .tmp then rename."""
        tmp = path.with_suffix(".tmp")
        async with aiofiles.open(tmp, "w") as f:
            await f.write(json.dumps(data, indent=2))
        tmp.replace(path)
