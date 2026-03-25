"""C1 — Config Loader.

Loads secrets from .env and settings from config.json.
Exposes a single validated Config dataclass to the rest of the system.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_CONFIG_PATH = Path(__file__).parent / "config.json"
_ENV_PATH = Path(__file__).parent / ".env"


@dataclass(frozen=True)
class Config:
    # ── Secrets (from .env) ───────────────────────────────────────────────
    discord_bot_token: str
    anthropic_api_key: str
    joplin_api_token: str

    # ── Discord ───────────────────────────────────────────────────────────
    discord_channel_id: int
    discord_user_id: int  # used for @mention in proactive messages
    security_alerts_channel_id: int | None  # optional; unauthorized-message alerts

    # ── User ──────────────────────────────────────────────────────────────
    user_name: str

    # ── Joplin ────────────────────────────────────────────────────────────
    joplin_host: str
    joplin_api_port: int
    todo_notebook: str  # only tasks from this Joplin notebook are used
    todo_inbox_note: str  # note title inside todo_notebook where `add:` tasks are appended

    # ── Timezone ──────────────────────────────────────────────────────────
    timezone: str

    # ── Schedule ──────────────────────────────────────────────────────────
    morning_routine: str               # "HH:MM"
    morning_routine_retry_window_min: int
    work_start: str                    # "HH:MM"
    work_end: str                      # "HH:MM"
    midday_checkin: str                # "HH:MM"
    evening_start: str                 # "HH:MM"
    end_of_day_review: str             # "HH:MM"
    bedtime: str                       # "HH:MM"

    # ── Nudge behaviour ───────────────────────────────────────────────────
    nudge_cooldown_min: int
    min_gap_for_nudge_min: int
    followup_default_min: int

    # ── Cost ──────────────────────────────────────────────────────────────
    monthly_cost_limit_usd: float

    # ── LLM ───────────────────────────────────────────────────────────────
    opus_session_max_messages: int

    # ── Behaviour ─────────────────────────────────────────────────────────
    weekend_evening_nudge: bool
    low_energy_tags: list[str] = field(default_factory=list)
    # Google Calendar IDs to exclude from all event fetching.
    # Run: docker compose exec bot python -m connectors.calendar  to list all IDs.
    excluded_calendar_ids: list[str] = field(default_factory=list)


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


def load_config(
    config_path: Path = _CONFIG_PATH,
    env_path: Path = _ENV_PATH,
) -> Config:
    """Load and validate configuration.

    Args:
        config_path: Path to config.json.
        env_path: Path to .env file.

    Returns:
        Validated Config instance.

    Raises:
        ConfigError: If required values are missing or invalid.
    """
    load_dotenv(dotenv_path=env_path)

    # ── Secrets from environment ──────────────────────────────────────────
    discord_bot_token = _require_env("DISCORD_BOT_TOKEN")
    anthropic_api_key = _require_env("ANTHROPIC_API_KEY")
    joplin_api_token = _require_env("JOPLIN_API_TOKEN")

    # ── Settings from config.json ─────────────────────────────────────────
    if not config_path.exists():
        raise ConfigError(
            f"config.json not found at {config_path}. "
            "Copy config.example.json to config.json and fill in your values."
        )

    with config_path.open() as f:
        raw = json.load(f)

    # Remove comment key if present
    raw.pop("_comment", None)

    try:
        return Config(
            discord_bot_token=discord_bot_token,
            anthropic_api_key=anthropic_api_key,
            joplin_api_token=joplin_api_token,
            discord_channel_id=_require_int(raw, "discord_channel_id"),
            discord_user_id=_require_int(raw, "discord_user_id"),
            security_alerts_channel_id=raw.get("security_alerts_channel_id") or None,
            user_name=_require_str(raw, "user_name"),
            joplin_host=raw.get("joplin_host", "joplin"),
            joplin_api_port=raw.get("joplin_api_port", 41184),
            todo_notebook=raw.get("todo_notebook", "00_TODO"),
            todo_inbox_note=raw.get("todo_inbox_note", "99 - added by eva"),
            timezone=raw.get("timezone", "Europe/Berlin"),
            morning_routine=raw.get("morning_routine", "07:30"),
            morning_routine_retry_window_min=raw.get("morning_routine_retry_window_min", 90),
            work_start=raw.get("work_start", "09:15"),
            work_end=raw.get("work_end", "16:00"),
            midday_checkin=raw.get("midday_checkin", "13:00"),
            evening_start=raw.get("evening_start", "20:30"),
            end_of_day_review=raw.get("end_of_day_review", "22:30"),
            bedtime=raw.get("bedtime", "23:00"),
            nudge_cooldown_min=raw.get("nudge_cooldown_min", 45),
            min_gap_for_nudge_min=raw.get("min_gap_for_nudge_min", 30),
            followup_default_min=raw.get("followup_default_min", 20),
            monthly_cost_limit_usd=float(raw.get("monthly_cost_limit_usd", 10.0)),
            opus_session_max_messages=raw.get("opus_session_max_messages", 10),
            weekend_evening_nudge=raw.get("weekend_evening_nudge", True),
            low_energy_tags=raw.get("low_energy_tags", ["[low-energy]", "[couch]", "[easy]"]),
            excluded_calendar_ids=raw.get("excluded_calendar_ids", []),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid config.json: {exc}") from exc


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise ConfigError(
            f"Required environment variable {key!r} is not set. "
            "Check your .env file."
        )
    return value


def _require_str(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"config.json: {key!r} must be a non-empty string.")
    return value


def _require_int(raw: dict, key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or value == 0:
        raise ConfigError(
            f"config.json: {key!r} must be a non-zero integer. "
            f"Got {value!r}."
        )
    return value
