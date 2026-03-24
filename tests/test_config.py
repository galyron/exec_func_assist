"""Tests for C1 — Config Loader."""

import json
import os
from pathlib import Path

import pytest

from config import ConfigError, load_config


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def valid_env(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-discord-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("JOPLIN_API_TOKEN", "test-joplin-token")


@pytest.fixture
def valid_config_json(tmp_path) -> Path:
    data = {
        "discord_channel_id": 123456789,
        "discord_user_id": 987654321,
        "user_name": "Gabriell",
        "joplin_host": "joplin",
        "joplin_api_port": 41184,
        "timezone": "Europe/Berlin",
        "morning_routine": "07:30",
        "morning_routine_retry_window_min": 90,
        "work_start": "09:15",
        "work_end": "16:00",
        "midday_checkin": "13:00",
        "evening_start": "20:30",
        "end_of_day_review": "22:30",
        "bedtime": "23:00",
        "nudge_cooldown_min": 45,
        "min_gap_for_nudge_min": 30,
        "followup_default_min": 20,
        "monthly_cost_limit_usd": 10.0,
        "opus_session_max_messages": 10,
        "weekend_evening_nudge": True,
        "low_energy_tags": ["[low-energy]", "[couch]", "[easy]"],
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def no_env_file(tmp_path) -> Path:
    """Non-existent .env path — env vars must come from monkeypatch."""
    return tmp_path / ".env"


# ── Happy path ────────────────────────────────────────────────────────────────

def test_loads_valid_config(valid_env, valid_config_json, no_env_file):
    config = load_config(config_path=valid_config_json, env_path=no_env_file)
    assert config.user_name == "Gabriell"
    assert config.discord_channel_id == 123456789
    assert config.discord_user_id == 987654321
    assert config.discord_bot_token == "test-discord-token"
    assert config.anthropic_api_key == "sk-ant-test"
    assert config.joplin_api_token == "test-joplin-token"
    assert config.monthly_cost_limit_usd == 10.0
    assert config.timezone == "Europe/Berlin"
    assert config.followup_default_min == 20


def test_loads_low_energy_tags(valid_env, valid_config_json, no_env_file):
    config = load_config(config_path=valid_config_json, env_path=no_env_file)
    assert "[couch]" in config.low_energy_tags
    assert "[low-energy]" in config.low_energy_tags


def test_config_is_immutable(valid_env, valid_config_json, no_env_file):
    config = load_config(config_path=valid_config_json, env_path=no_env_file)
    with pytest.raises((AttributeError, TypeError)):
        config.user_name = "someone_else"  # type: ignore[misc]


def test_ignores_comment_key(valid_env, tmp_path, no_env_file):
    data = {
        "_comment": "this should be ignored",
        "discord_channel_id": 111,
        "discord_user_id": 222,
        "user_name": "Gabriell",
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    # Should not raise
    config = load_config(config_path=path, env_path=no_env_file)
    assert config.user_name == "Gabriell"


# ── Missing secrets ───────────────────────────────────────────────────────────

def test_missing_discord_token_raises(monkeypatch, valid_config_json, no_env_file):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("JOPLIN_API_TOKEN", "test-joplin-token")
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="DISCORD_BOT_TOKEN"):
        load_config(config_path=valid_config_json, env_path=no_env_file)


def test_missing_anthropic_key_raises(monkeypatch, valid_config_json, no_env_file):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-discord-token")
    monkeypatch.setenv("JOPLIN_API_TOKEN", "test-joplin-token")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        load_config(config_path=valid_config_json, env_path=no_env_file)


# ── Missing / invalid config.json fields ─────────────────────────────────────

def test_missing_config_file_raises(valid_env, tmp_path, no_env_file):
    with pytest.raises(ConfigError, match="config.json not found"):
        load_config(config_path=tmp_path / "nonexistent.json", env_path=no_env_file)


def test_zero_discord_channel_id_raises(valid_env, tmp_path, no_env_file):
    data = {"discord_channel_id": 0, "discord_user_id": 123, "user_name": "Gabriell"}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="discord_channel_id"):
        load_config(config_path=path, env_path=no_env_file)


def test_missing_user_name_raises(valid_env, tmp_path, no_env_file):
    data = {"discord_channel_id": 123, "discord_user_id": 456, "user_name": ""}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="user_name"):
        load_config(config_path=path, env_path=no_env_file)


# ── Defaults ──────────────────────────────────────────────────────────────────

def test_defaults_applied_when_optional_keys_absent(valid_env, tmp_path, no_env_file):
    data = {"discord_channel_id": 123, "discord_user_id": 456, "user_name": "Gabriell"}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    config = load_config(config_path=path, env_path=no_env_file)
    assert config.joplin_host == "joplin"
    assert config.joplin_api_port == 41184
    assert config.timezone == "Europe/Berlin"
    assert config.followup_default_min == 20
    assert config.monthly_cost_limit_usd == 10.0
    assert config.weekend_evening_nudge is True
    assert config.low_energy_tags == ["[low-energy]", "[couch]", "[easy]"]
