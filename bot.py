"""Entry point — EFA Discord Bot.

Usage:
    python bot.py                              # production
    python bot.py --debug                      # debug mode, clock starts now
    python bot.py --debug --debug-time "2026-03-24 07:25"
    python bot.py --debug --debug-time "2026-03-24 07:25" --debug-multiplier 120
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import discord

from config import ConfigError, load_config
from state.manager import StateManager
from utils.clock import Clock, DebugClock, RealClock

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("efa.bot")


class EFABot(discord.Client):
    """Executive Function Assistant Discord bot.

    Routes messages from the configured channel and DMs through the same
    handler so behaviour is identical regardless of message source (D7).
    """

    def __init__(
        self,
        config,
        state: StateManager,
        clock: Clock,
        *,
        debug: bool = False,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self.state = state
        self.clock = clock
        self.debug = debug

    # ── Discord lifecycle ─────────────────────────────────────────────────────

    async def setup_hook(self) -> None:
        """Called by discord.py before login completes."""
        await self.state.initialize()
        if await self.state.is_first_run():
            log.info("First run detected — state files initialised.")
            await self.state.mark_first_run_complete()

    async def on_ready(self) -> None:
        mode = "DEBUG" if self.debug else "PRODUCTION"
        log.info(
            "Bot ready as %s | mode=%s | user=%s | channel=%s",
            self.user,
            mode,
            self.config.user_name,
            self.config.discord_channel_id,
        )
        if self.debug:
            log.info(
                "Debug clock: simulated time = %s (multiplier=%.1fx)",
                self.clock.now().strftime("%Y-%m-%d %H:%M:%S %Z"),
                self.clock.multiplier if isinstance(self.clock, DebugClock) else 1.0,
            )

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_configured_channel = (
            isinstance(message.channel, discord.TextChannel)
            and message.channel.id == self.config.discord_channel_id
        )

        if not (is_dm or is_configured_channel):
            return

        await self._handle_message(message)

    # ── Message handling ──────────────────────────────────────────────────────

    async def _handle_message(self, message: discord.Message) -> None:
        """Single entry point for all user messages — channel and DM alike."""
        # Phase 1-A: echo only.
        # In later phases this will be replaced by the Intent Router.
        source = "DM" if isinstance(message.channel, discord.DMChannel) else "channel"
        log.info("Message from %s (%s): %s", message.author, source, message.content)

        await message.reply(
            f"Echo, {self.config.user_name}: {message.content}"
        )


# ── Entry point ───────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Executive Function Assistant Bot")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug/time-simulation mode.",
    )
    parser.add_argument(
        "--debug-time",
        metavar="YYYY-MM-DD HH:MM",
        help="Simulated start datetime (requires --debug).",
    )
    parser.add_argument(
        "--debug-multiplier",
        type=float,
        default=60.0,
        metavar="N",
        help="Simulated minutes per real minute (default 60 → 1 real min = 1 sim hour).",
    )
    return parser.parse_args()


def _build_clock(args: argparse.Namespace, timezone: str) -> Clock:
    tz = ZoneInfo(timezone)
    if not args.debug:
        return RealClock(timezone)

    if args.debug_time:
        start = datetime.strptime(args.debug_time, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    else:
        start = datetime.now(tz)

    log.info(
        "Debug mode: simulated start=%s, multiplier=%.1fx",
        start.strftime("%Y-%m-%d %H:%M %Z"),
        args.debug_multiplier,
    )
    return DebugClock(start_time=start, multiplier=args.debug_multiplier)


def main() -> None:
    args = _parse_args()

    try:
        config = load_config()
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        raise SystemExit(1) from exc

    clock = _build_clock(args, config.timezone)
    state = StateManager(clock=clock)
    bot = EFABot(config, state, clock, debug=args.debug)

    bot.run(config.discord_bot_token, log_handler=None)


if __name__ == "__main__":
    main()
