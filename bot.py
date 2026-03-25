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
from connectors.calendar import CalendarConnector
from connectors.joplin import JoplinConnector
from context.assembler import ContextAssembler
from handlers.bedtime import BedtimeHandler
from handlers.checkin import CheckinHandler
from handlers.followup import FollowupHandler
from handlers.kickoff import KickoffHandler
from handlers.morning import MorningRoutineHandler
from handlers.on_demand import OnDemandHandler
from llm.client import LLMClient
from scheduler import Scheduler
from state.manager import StateManager
from utils.clock import Clock, DebugClock, RealClock

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("efa.bot")

_TOKEN_PATH = Path("secrets/google_token.json")


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
        joplin: JoplinConnector,
        calendar: CalendarConnector,
        assembler: ContextAssembler,
        llm: LLMClient,
        morning_handler: MorningRoutineHandler,
        kickoff_handler: KickoffHandler,
        checkin_handler: CheckinHandler,
        bedtime_handler: BedtimeHandler,
        on_demand_handler: OnDemandHandler,
        followup_handler: FollowupHandler,
        *,
        debug: bool = False,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self.state = state
        self.clock = clock
        self.joplin = joplin
        self.calendar = calendar
        self.assembler = assembler
        self.llm = llm
        self.morning_handler = morning_handler
        self.kickoff_handler = kickoff_handler
        self.checkin_handler = checkin_handler
        self.bedtime_handler = bedtime_handler
        self.on_demand_handler = on_demand_handler
        self.followup_handler = followup_handler
        self.debug = debug
        self._scheduler: Scheduler | None = None

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
            self.user, mode, self.config.user_name, self.config.discord_channel_id,
        )
        if self.debug:
            log.info(
                "Debug clock: simulated time = %s (multiplier=%.1fx)",
                self.clock.now().strftime("%Y-%m-%d %H:%M:%S %Z"),
                self.clock.multiplier if isinstance(self.clock, DebugClock) else 1.0,
            )

        self._scheduler = Scheduler(
            config=self.config,
            get_send_fn=self._get_channel_send,
            morning_handler=self.morning_handler,
            kickoff_handler=self.kickoff_handler,
            checkin_handler=self.checkin_handler,
            bedtime_handler=self.bedtime_handler,
        )
        self._scheduler.start()
        self.followup_handler.set_apscheduler(self._scheduler._scheduler)
        self.on_demand_handler.set_scheduler(self._scheduler)

    async def close(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown()
        await super().close()

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return

        if message.author.id != self.config.discord_user_id:
            await self._alert_unauthorized(message)
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
        text = message.content.strip()

        # Morning routine takes routing priority when active
        if await self.morning_handler.is_active():
            done = await self.morning_handler.handle_response(text, message.reply)
            if done:
                log.info("Morning routine complete.")
            return

        # All other messages route through the on-demand handler (C12)
        await self.on_demand_handler.handle(text, message.reply)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _alert_unauthorized(self, message: discord.Message) -> None:
        """Log and optionally notify about a message from an unexpected user."""
        source = (
            "DM" if isinstance(message.channel, discord.DMChannel)
            else f"#{message.channel}"
        )
        log.warning(
            "Unauthorized message ignored | user=%s (id=%s) | source=%s | text=%r",
            message.author, message.author.id, source, message.content[:200],
        )
        if not self.config.security_alerts_channel_id:
            return
        alert_ch = self.get_channel(self.config.security_alerts_channel_id)
        if alert_ch is None:
            log.warning("security_alerts_channel_id configured but channel not found.")
            return
        preview = message.content[:200] + ("…" if len(message.content) > 200 else "")
        await alert_ch.send(
            f"⚠️ **Unauthorized message**\n"
            f"**User:** {message.author} (ID `{message.author.id}`)\n"
            f"**Source:** {source}\n"
            f"**Content:** {preview or '*(empty)*'}"
        )

    def _get_channel_send(self):
        """Return channel.send or None if the channel isn't available."""
        channel = self.get_channel(self.config.discord_channel_id)
        return channel.send if channel else None


# ── Factory ───────────────────────────────────────────────────────────────────

def _build_bot(args: argparse.Namespace, config, clock: Clock) -> EFABot:
    state = StateManager(clock=clock)

    joplin = JoplinConnector(
        host=config.joplin_host,
        port=config.joplin_api_port,
        token=config.joplin_api_token,
        notebook=config.todo_notebook,
        inbox_note=config.todo_inbox_note,
    )
    calendar = CalendarConnector(
        token_path=_TOKEN_PATH,
        timezone=config.timezone,
        excluded_calendar_ids=config.excluded_calendar_ids,
        min_gap_min=config.min_gap_for_nudge_min,
    )
    assembler = ContextAssembler(config=config, state_manager=state, clock=clock)
    llm = LLMClient(config=config, state_manager=state)

    async def build_context():
        tasks, events, interactions = await asyncio.gather(
            joplin.get_tasks(),
            calendar.get_events(),
            state.get_recent_interactions(5),
        )
        return await assembler.assemble(tasks, events, interactions)

    morning = MorningRoutineHandler(
        config=config, state_manager=state, clock=clock,
        llm_client=llm, context_builder=build_context,
    )
    kickoff = KickoffHandler(
        config=config, state_manager=state, clock=clock,
        llm_client=llm, context_builder=build_context,
    )
    checkin = CheckinHandler(
        config=config, state_manager=state, clock=clock,
        llm_client=llm, context_builder=build_context,
    )
    bedtime = BedtimeHandler(
        config=config, state_manager=state, clock=clock,
        llm_client=llm, context_builder=build_context, calendar=calendar,
    )
    followup = FollowupHandler(
        config=config, state_manager=state, clock=clock,
        get_send_fn=lambda: None,  # overwritten below once bot is constructed
    )
    on_demand = OnDemandHandler(
        config=config, state_manager=state, clock=clock,
        llm_client=llm, context_builder=build_context,
        followup_handler=followup, joplin=joplin, calendar=calendar,
    )

    bot = EFABot(
        config, state, clock,
        joplin, calendar, assembler, llm,
        morning, kickoff, checkin, bedtime,
        on_demand, followup,
        debug=args.debug,
    )
    # Wire followup's channel-send getter to the live bot instance
    followup._get_send_fn = bot._get_channel_send
    return bot


# ── Entry point ───────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Executive Function Assistant Bot")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug/time-simulation mode.")
    parser.add_argument("--debug-time", metavar="YYYY-MM-DD HH:MM",
                        help="Simulated start datetime (requires --debug).")
    parser.add_argument("--debug-multiplier", type=float, default=60.0, metavar="N",
                        help="Simulated minutes per real minute (default 60).")
    return parser.parse_args()


def _build_clock(args: argparse.Namespace, timezone: str) -> Clock:
    tz = ZoneInfo(timezone)
    if not args.debug:
        return RealClock(timezone)

    if args.debug_time:
        start = datetime.strptime(args.debug_time, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    else:
        start = datetime.now(tz)

    log.info("Debug mode: simulated start=%s, multiplier=%.1fx",
             start.strftime("%Y-%m-%d %H:%M %Z"), args.debug_multiplier)
    return DebugClock(start_time=start, multiplier=args.debug_multiplier)


def main() -> None:
    args = _parse_args()

    try:
        config = load_config()
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        raise SystemExit(1) from exc

    clock = _build_clock(args, config.timezone)
    bot = _build_bot(args, config, clock)
    bot.run(config.discord_bot_token, log_handler=None)


if __name__ == "__main__":
    main()
