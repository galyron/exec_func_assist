"""Shared base class and types for all handlers."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from config import Config
from state.manager import StateManager
from utils.clock import Clock

# A coroutine that sends a message — e.g. channel.send or message.reply
SendFn = Callable[..., Awaitable[Any]]

log = logging.getLogger(__name__)


class BaseHandler:
    """Common dependencies and interaction-logging helpers shared by all handlers."""

    def __init__(self, config: Config, state_manager: StateManager, clock: Clock) -> None:
        self._config = config
        self._state = state_manager
        self._clock = clock

    async def _log_bot(self, content: str) -> None:
        await self._state.append_interaction({
            "timestamp": self._clock.now().isoformat(),
            "direction": "bot",
            "content": content[:500],  # cap to avoid bloating interaction log
            "user_id": "default",
        })

    async def _log_user(self, content: str) -> None:
        await self._state.append_interaction({
            "timestamp": self._clock.now().isoformat(),
            "direction": "user",
            "content": content[:500],
            "user_id": "default",
        })
