"""Tests for src/garmin/client.py (unit tests with mocks)."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.garmin.client import (
    ActivityData,
    GarminClient,
    SleepData,
    _assess_sleep_quality,
)


def test_assess_sleep_quality():
    assert _assess_sleep_quality(85) == "Excelente"
    assert _assess_sleep_quality(75) == "Bom"
    assert _assess_sleep_quality(65) == "Razoável"
    assert _assess_sleep_quality(50) == "Mau"
    assert _assess_sleep_quality(None) is None


def _make_client() -> GarminClient:
    return GarminClient("test@example.com", "password")


def test_get_sleep_data_parses_response():
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_sleep_data.return_value = {
        "dailySleepDTO": {
            "sleepTimeSeconds": 27000,  # 7.5 hours
            "sleepScores": {"overall": {"value": 82}},
        }
    }
    client._client = mock_garmin

    result = client.get_sleep_data(date(2026, 2, 12))

    assert result.hours == 7.5
    assert result.score == 82
    assert result.quality == "Excelente"


def test_get_sleep_data_empty_response():
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_sleep_data.return_value = {}
    client._client = mock_garmin

    result = client.get_sleep_data(date(2026, 2, 12))

    assert result.hours is None
    assert result.score is None


def test_get_activity_data_parses_response():
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_stats.return_value = {
        "totalSteps": 12340,
        "activeKilocalories": 487,
        "bmrKilocalories": 1680,
    }
    client._client = mock_garmin

    result = client.get_activity_data(date(2026, 2, 12))

    assert result.steps == 12340
    assert result.active_calories == 487
    assert result.resting_calories == 1680


def test_get_yesterday_summary_uses_today_for_sleep():
    """Sleep must be queried with today's date; activity with yesterday's date.

    Garmin assigns last night's sleep to the wake-up date (today), so querying
    yesterday would return the previous night's sleep instead.
    """
    from unittest.mock import call, patch
    from datetime import date as date_type
    import datetime as dt_module

    fixed_today = date_type(2026, 2, 13)  # Friday
    fixed_yesterday = date_type(2026, 2, 12)  # Thursday

    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_sleep_data.return_value = {
        "dailySleepDTO": {"sleepTimeSeconds": 24120, "sleepScores": {"overall": {"value": 72}}}
    }
    mock_garmin.get_stats.return_value = {
        "totalSteps": 9000, "activeKilocalories": 350, "bmrKilocalories": 1700
    }
    client._client = mock_garmin

    with patch("src.garmin.client.date") as mock_date:
        mock_date.today.return_value = fixed_today
        mock_date.side_effect = lambda *a, **kw: date_type(*a, **kw)
        summary = client.get_yesterday_summary()

    # Sleep queried with today (Friday), activity with yesterday (Thursday)
    mock_garmin.get_sleep_data.assert_called_once_with(fixed_today.isoformat())
    mock_garmin.get_stats.assert_any_call(fixed_yesterday.isoformat())

    # Summary is stored under yesterday's date
    assert summary.date == fixed_yesterday
    assert summary.sleep.hours is not None
    assert summary.activity.steps == 9000


def test_get_yesterday_summary_partial_failure():
    """Should return partial data if one of the calls fails."""
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_sleep_data.return_value = {
        "dailySleepDTO": {"sleepTimeSeconds": 25200, "sleepScores": {"overall": {"value": 70}}}
    }
    mock_garmin.get_stats.side_effect = Exception("network error")
    client._client = mock_garmin

    summary = client.get_yesterday_summary()

    assert summary.sleep.hours is not None
    assert summary.activity.steps is None


def test_to_metrics_dict():
    from src.garmin.client import DailySummary
    client = _make_client()
    summary = DailySummary(
        date=date(2026, 2, 12),
        sleep=SleepData(hours=7.5, score=82, quality="Excelente"),
        activity=ActivityData(steps=10000, active_calories=400, resting_calories=1700),
    )
    d = client.to_metrics_dict(summary)
    assert d["sleep_hours"] == 7.5
    assert d["steps"] == 10000
    assert d["garmin_sync_success"] is True
