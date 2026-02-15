"""Tests for src/config.py."""

import os
import pytest
from unittest.mock import patch

from src.config import Config, ConfigError, load_config


def _base_env() -> dict:
    return {
        "GARMIN_EMAIL": "test@example.com",
        "GARMIN_PASSWORD": "secret",
        "TELEGRAM_BOT_TOKEN": "123:ABC",
        "TELEGRAM_CHAT_ID": "999",
        "DATABASE_PATH": "/tmp/test.db",
        "LOG_FILE": "/tmp/test.log",
        "DAILY_SYNC_TIME": "07:00",
        "DAILY_REPORT_TIME": "08:30",
        "WEEKLY_REPORT_DAY": "sunday",
        "WEEKLY_REPORT_TIME": "20:00",
        "TIMEZONE": "Europe/Lisbon",
        "LOG_LEVEL": "DEBUG",
    }


def test_load_config_success():
    with patch.dict(os.environ, _base_env(), clear=True):
        config = load_config()
    assert config.garmin_email == "test@example.com"
    assert config.sync_hour == 7
    assert config.sync_minute == 0
    assert config.report_hour == 8
    assert config.report_minute == 30


def test_load_config_missing_required():
    env = _base_env()
    del env["GARMIN_EMAIL"]
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigError, match="GARMIN_EMAIL"):
            load_config()


def test_load_config_bad_time_format():
    env = _base_env()
    env["DAILY_SYNC_TIME"] = "7am"
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigError, match="DAILY_SYNC_TIME"):
            load_config()


def test_load_config_defaults():
    env = {
        "GARMIN_EMAIL": "a@b.com",
        "GARMIN_PASSWORD": "pw",
        "TELEGRAM_BOT_TOKEN": "tok",
        "TELEGRAM_CHAT_ID": "1",
    }
    with patch.dict(os.environ, env, clear=True):
        config = load_config()
    assert config.timezone == "Europe/Lisbon"
    assert config.daily_sync_time == "07:00"
    assert config.log_level == "INFO"
    # New field defaults
    assert config.sync_retry_delay_minutes == 30
    assert config.health_port is None
    assert config.daily_alerts is True


def test_load_config_new_fields_from_env():
    env = _base_env()
    env["SYNC_RETRY_DELAY_MINUTES"] = "45"
    env["HEALTH_PORT"] = "8080"
    env["DAILY_ALERTS"] = "false"
    with patch.dict(os.environ, env, clear=True):
        config = load_config()
    assert config.sync_retry_delay_minutes == 45
    assert config.health_port == 8080
    assert config.daily_alerts is False


def test_load_config_daily_alerts_true_variants():
    """DAILY_ALERTS should be True for any value other than 'false'."""
    env = _base_env()
    for value in ("true", "True", "TRUE", "1", "yes"):
        env["DAILY_ALERTS"] = value
        with patch.dict(os.environ, env, clear=True):
            config = load_config()
        assert config.daily_alerts is True, f"Expected True for DAILY_ALERTS={value!r}"


def test_load_config_health_port_invalid():
    env = _base_env()
    env["HEALTH_PORT"] = "not-a-number"
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigError, match="HEALTH_PORT"):
            load_config()


def test_load_config_gym_equipment_from_env():
    env = _base_env()
    env["GYM_EQUIPMENT"] = "dumbbells, bench, bands"
    env["GYM_TRAINING_MINUTES"] = "60"
    with patch.dict(os.environ, env, clear=True):
        config = load_config()
    assert config.gym_equipment == "dumbbells, bench, bands"
    assert config.gym_training_minutes == 60


def test_load_config_gym_equipment_default_none():
    with patch.dict(os.environ, _base_env(), clear=True):
        config = load_config()
    assert config.gym_equipment is None
    assert config.gym_training_minutes == 45


def test_load_config_gym_equipment_empty_string_is_none():
    env = _base_env()
    env["GYM_EQUIPMENT"] = ""
    with patch.dict(os.environ, env, clear=True):
        config = load_config()
    assert config.gym_equipment is None
