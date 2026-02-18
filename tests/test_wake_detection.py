"""Tests for wake detection: config, repository tracking, garmin sleep check, and scheduler jobs."""

import os
import tempfile
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.config import load_config
from src.database.repository import Repository
from src.garmin.client import GarminClient


# ------------------------------------------------------------------ #
# Config tests                                                         #
# ------------------------------------------------------------------ #

def _base_env() -> dict:
    return {
        "GARMIN_EMAIL": "test@example.com",
        "GARMIN_PASSWORD": "secret",
        "TELEGRAM_BOT_TOKEN": "123:ABC",
        "TELEGRAM_CHAT_ID": "999",
        "DATABASE_PATH": "/tmp/test.db",
        "LOG_FILE": "/tmp/test.log",
    }


def test_wake_detection_defaults():
    with patch.dict(os.environ, _base_env(), clear=True):
        config = load_config()
    assert config.wake_detection is True
    assert config.wake_check_interval_minutes == 10
    assert config.wake_check_start == "05:00"
    assert config.wake_check_end == "12:00"
    assert config.wake_start_hour == 5
    assert config.wake_start_minute == 0
    assert config.wake_end_hour == 12
    assert config.wake_end_minute == 0


def test_wake_detection_disabled():
    env = _base_env()
    env["WAKE_DETECTION"] = "false"
    with patch.dict(os.environ, env, clear=True):
        config = load_config()
    assert config.wake_detection is False


def test_wake_detection_custom_values():
    env = _base_env()
    env["WAKE_CHECK_INTERVAL_MINUTES"] = "15"
    env["WAKE_CHECK_START"] = "06:30"
    env["WAKE_CHECK_END"] = "11:00"
    with patch.dict(os.environ, env, clear=True):
        config = load_config()
    assert config.wake_check_interval_minutes == 15
    assert config.wake_start_hour == 6
    assert config.wake_start_minute == 30
    assert config.wake_end_hour == 11
    assert config.wake_end_minute == 0


# ------------------------------------------------------------------ #
# Repository: report tracking tests                                    #
# ------------------------------------------------------------------ #

@pytest.fixture
def repo():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    r = Repository(db_path)
    r.init_database()
    yield r
    r._engine.dispose()
    try:
        os.unlink(db_path)
    except PermissionError:
        pass


def test_has_report_sent_today_initially_false(repo):
    assert repo.has_report_sent_today() is False


def test_log_report_sent_marks_today(repo):
    repo.log_report_sent()
    assert repo.has_report_sent_today() is True


def test_has_report_sent_today_ignores_sync_logs(repo):
    """A normal sync 'success' log should not count as report sent."""
    repo.log_sync("success")
    assert repo.has_report_sent_today() is False


# ------------------------------------------------------------------ #
# GarminClient: check_sleep_available                                  #
# ------------------------------------------------------------------ #

def _make_client() -> GarminClient:
    return GarminClient("test@example.com", "password")


def test_check_sleep_available_true():
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_sleep_data.return_value = {
        "dailySleepDTO": {
            "sleepTimeSeconds": 27000,
            "sleepScores": {"overall": {"value": 82}},
        }
    }
    client._client = mock_garmin

    assert client.check_sleep_available(date(2026, 2, 15)) is True


def test_check_sleep_available_false_no_data():
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_sleep_data.return_value = {}
    client._client = mock_garmin

    assert client.check_sleep_available(date(2026, 2, 15)) is False


def test_check_sleep_available_false_no_seconds():
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_sleep_data.return_value = {
        "dailySleepDTO": {"sleepTimeSeconds": None}
    }
    client._client = mock_garmin

    assert client.check_sleep_available(date(2026, 2, 15)) is False


def test_check_sleep_available_false_zero_seconds():
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_sleep_data.return_value = {
        "dailySleepDTO": {"sleepTimeSeconds": 0}
    }
    client._client = mock_garmin

    assert client.check_sleep_available(date(2026, 2, 15)) is False


def test_check_sleep_available_false_on_exception():
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_sleep_data.side_effect = Exception("network error")
    client._client = mock_garmin

    assert client.check_sleep_available(date(2026, 2, 15)) is False


# ------------------------------------------------------------------ #
# Wake check job tests                                                 #
# ------------------------------------------------------------------ #

from src.scheduler.jobs import make_wake_check_job, make_wake_fallback_job


def test_wake_check_job_skips_if_report_already_sent():
    garmin = MagicMock()
    repo = MagicMock()
    repo.has_report_sent_today.return_value = True

    job = make_wake_check_job(garmin, repo)
    job()

    # Should not check Garmin at all
    garmin.check_sleep_available.assert_not_called()


def test_wake_check_job_skips_if_no_sleep_data():
    garmin = MagicMock()
    repo = MagicMock()
    repo.has_report_sent_today.return_value = False
    garmin.check_sleep_available.return_value = False

    job = make_wake_check_job(garmin, repo)
    job()

    # Should not sync
    garmin.get_yesterday_summary.assert_not_called()


def test_wake_check_job_triggers_sync_on_wake():
    """Wake check syncs data and marks as sent (no auto-report)."""
    from src.garmin.client import DailySummary, SleepData, ActivityData

    garmin = MagicMock()
    repo = MagicMock()
    repo.has_report_sent_today.return_value = False
    garmin.check_sleep_available.return_value = True

    yesterday = date.today() - __import__("datetime").timedelta(days=1)
    garmin.get_yesterday_summary.return_value = DailySummary(
        date=yesterday,
        sleep=SleepData(hours=7.5, score=82, quality="Excelente"),
        activity=ActivityData(steps=10000, active_calories=400, resting_calories=1700),
    )
    garmin.to_metrics_dict.return_value = {
        "sleep_hours": 7.5, "garmin_sync_success": True,
    }

    job = make_wake_check_job(garmin, repo)
    job()

    # Should have synced
    garmin.get_yesterday_summary.assert_called_once()
    repo.save_daily_metrics.assert_called_once()
    # Should mark as sent to prevent fallback double-sync
    repo.log_report_sent.assert_called_once()


def test_wake_fallback_job_skips_if_report_sent():
    garmin = MagicMock()
    repo = MagicMock()
    repo.has_report_sent_today.return_value = True

    job = make_wake_fallback_job(garmin, repo)
    job()

    garmin.get_yesterday_summary.assert_not_called()


def test_wake_fallback_job_forces_sync():
    """Fallback syncs data (no auto-report)."""
    from src.garmin.client import DailySummary, SleepData, ActivityData

    garmin = MagicMock()
    repo = MagicMock()
    repo.has_report_sent_today.return_value = False

    yesterday = date.today() - __import__("datetime").timedelta(days=1)
    garmin.get_yesterday_summary.return_value = DailySummary(
        date=yesterday,
        sleep=SleepData(hours=None, score=None, quality=None),
        activity=ActivityData(steps=8000, active_calories=300, resting_calories=1600),
    )
    garmin.to_metrics_dict.return_value = {
        "sleep_hours": None, "garmin_sync_success": True,
    }

    job = make_wake_fallback_job(garmin, repo)
    job()

    garmin.get_yesterday_summary.assert_called_once()
    repo.save_daily_metrics.assert_called_once()
    # Fallback does NOT call log_report_sent (user triggers /sync manually)
    repo.log_report_sent.assert_not_called()
