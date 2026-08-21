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
        "totalKilocalories": 2100,
    }
    client._client = mock_garmin

    result = client.get_activity_data(date(2026, 2, 12))

    assert result.steps == 12340
    assert result.active_calories == 487
    assert result.resting_calories == 1680
    assert result.total_calories == 2100


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


def test_get_health_data_body_battery_uses_level_trend_not_charged_total():
    """Prove-It: get_body_battery returns ONE summary object per day whose
    "charged"/"drained" fields are cumulative gain/loss totals, not level
    readings. The old code took max/min of a 1-item list of "charged" values,
    which are always equal — producing the observed "68-68" bug. The real
    high/low must come from bodyBatteryValuesArray (the [timestamp, level]
    trend), matching real production data: min 10, max 87 on 2026-08-19."""
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_stats.return_value = {}
    mock_garmin.get_stress_data.return_value = {}
    mock_garmin.get_body_battery.return_value = [{
        "date": "2026-08-19",
        "charged": 66,
        "drained": 77,
        "bodyBatteryValuesArray": [
            [1787094000000, 21],
            [1787121720000, 85],
            [1787123520000, 87],
            [1787151060000, 44],
            [1787151600000, 44],
            [1787179500000, 10],
        ],
        "bodyBatteryValueDescriptorDTOList": [
            {"bodyBatteryValueDescriptorIndex": 0, "bodyBatteryValueDescriptorKey": "timestamp"},
            {"bodyBatteryValueDescriptorIndex": 1, "bodyBatteryValueDescriptorKey": "bodyBatteryLevel"},
        ],
    }]
    client._client = mock_garmin

    result = client.get_health_data(date(2026, 8, 19))

    assert result["body_battery_low"] == 10
    assert result["body_battery_high"] == 87


def test_get_health_data_body_battery_empty_values_array_stays_none():
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_stats.return_value = {}
    mock_garmin.get_stress_data.return_value = {}
    mock_garmin.get_body_battery.return_value = [{
        "date": "2026-01-15", "charged": 0, "drained": 0, "bodyBatteryValuesArray": [],
    }]
    client._client = mock_garmin

    result = client.get_health_data(date(2026, 1, 15))

    assert result["body_battery_high"] is None
    assert result["body_battery_low"] is None


def test_get_health_data_body_battery_empty_list_response():
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_stats.return_value = {}
    mock_garmin.get_stress_data.return_value = {}
    mock_garmin.get_body_battery.return_value = []
    client._client = mock_garmin

    result = client.get_health_data(date(2026, 8, 19))

    assert result["body_battery_high"] is None
    assert result["body_battery_low"] is None


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


def test_to_metrics_dict_includes_hydration():
    from src.garmin.client import DailySummary
    client = _make_client()
    summary = DailySummary(
        date=date(2026, 2, 12),
        sleep=SleepData(hours=7.5, score=82, quality="Excelente"),
        activity=ActivityData(steps=10000, active_calories=400, resting_calories=1700),
        hydration_ml=473,
        hydration_goal_ml=2839,
    )
    d = client.to_metrics_dict(summary)
    assert d["hydration_ml"] == 473
    assert d["hydration_goal_ml"] == 2839


def test_to_metrics_dict_includes_blood_pressure():
    from src.garmin.client import DailySummary
    client = _make_client()
    summary = DailySummary(
        date=date(2026, 2, 12),
        sleep=SleepData(hours=7.5, score=82, quality="Excelente"),
        activity=ActivityData(steps=10000, active_calories=400, resting_calories=1700),
        blood_pressure_systolic=118,
        blood_pressure_diastolic=72,
        blood_pressure_pulse=60,
    )
    d = client.to_metrics_dict(summary)
    assert d["blood_pressure_systolic"] == 118
    assert d["blood_pressure_diastolic"] == 72
    assert d["blood_pressure_pulse"] == 60


def test_get_health_data_parses_hydration():
    """Prove-It: get_hydration_data returns valueInML/goalInML as floats
    (e.g. 473.176) — confirmed against production 2026-08-21 response."""
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_stats.return_value = {}
    mock_garmin.get_stress_data.return_value = {}
    mock_garmin.get_body_battery.return_value = []
    mock_garmin.get_spo2_data.return_value = {}
    mock_garmin.get_intensity_minutes_data.return_value = {}
    mock_garmin.get_hydration_data.return_value = {
        "userId": 111807159,
        "calendarDate": "2026-08-21",
        "valueInML": 473.176,
        "goalInML": 2839.056,
        "dailyAverageinML": None,
        "lastEntryTimestampLocal": "2026-08-21T09:25:01.484",
        "sweatLossInML": None,
        "activityIntakeInML": 0.0,
    }
    client._client = mock_garmin

    result = client.get_health_data(date(2026, 8, 21))

    assert result["hydration_ml"] == 473
    assert result["hydration_goal_ml"] == 2839


def test_get_health_data_hydration_null_stays_none():
    """A day with no logged hydration returns null fields (confirmed against
    production for a day before the user started logging water)."""
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_stats.return_value = {}
    mock_garmin.get_stress_data.return_value = {}
    mock_garmin.get_body_battery.return_value = []
    mock_garmin.get_spo2_data.return_value = {}
    mock_garmin.get_intensity_minutes_data.return_value = {}
    mock_garmin.get_hydration_data.return_value = {
        "userId": 111807159,
        "calendarDate": "2026-08-01",
        "valueInML": None,
        "goalInML": 2839.056,
        "dailyAverageinML": None,
        "lastEntryTimestampLocal": None,
        "sweatLossInML": None,
        "activityIntakeInML": None,
    }
    client._client = mock_garmin

    result = client.get_health_data(date(2026, 8, 1))

    assert result["hydration_ml"] is None


def test_get_health_data_hydration_api_failure_stays_none():
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_stats.return_value = {}
    mock_garmin.get_stress_data.return_value = {}
    mock_garmin.get_body_battery.return_value = []
    mock_garmin.get_spo2_data.return_value = {}
    mock_garmin.get_intensity_minutes_data.return_value = {}
    mock_garmin.get_hydration_data.side_effect = Exception("API error")
    client._client = mock_garmin

    result = client.get_health_data(date(2026, 8, 21))

    assert result["hydration_ml"] is None
    assert result["hydration_goal_ml"] is None


def _bp_mock(get_blood_pressure_return):
    mock_garmin = MagicMock()
    mock_garmin.get_stats.return_value = {}
    mock_garmin.get_stress_data.return_value = {}
    mock_garmin.get_body_battery.return_value = []
    mock_garmin.get_spo2_data.return_value = {}
    mock_garmin.get_intensity_minutes_data.return_value = {}
    mock_garmin.get_hydration_data.return_value = {}
    mock_garmin.get_blood_pressure.return_value = get_blood_pressure_return
    return mock_garmin


def test_get_health_data_parses_single_blood_pressure_reading():
    """Confirmed against production 2026-08-21: a single-reading day has
    highSystolic == lowSystolic (numOfMeasurements: 1)."""
    client = _make_client()
    client._client = _bp_mock({
        "measurementSummaries": [{
            "measurements": [{
                "systolic": 125,
                "diastolic": 75,
                "pulse": 66,
                "measurementTimestampLocal": "2026-08-21T10:40:36.86",
            }]
        }]
    })

    result = client.get_health_data(date(2026, 8, 21))

    assert result["blood_pressure_systolic"] == 125
    assert result["blood_pressure_diastolic"] == 75
    assert result["blood_pressure_pulse"] == 66


def test_get_health_data_multiple_readings_uses_latest_not_high():
    """Prove-It: with two readings the same day, /hoje must show the LATEST
    one, not the day's high — mirrors the Body Battery high/low bug (project
    lesson: never take max/min of per-day summary fields as 'the' reading)."""
    client = _make_client()
    client._client = _bp_mock({
        "measurementSummaries": [{
            "measurements": [
                {
                    "systolic": 140,
                    "diastolic": 90,
                    "pulse": 80,
                    "measurementTimestampLocal": "2026-08-21T08:00:00.00",
                },
                {
                    "systolic": 118,
                    "diastolic": 72,
                    "pulse": 60,
                    "measurementTimestampLocal": "2026-08-21T20:00:00.00",
                },
            ]
        }]
    })

    result = client.get_health_data(date(2026, 8, 21))

    assert result["blood_pressure_systolic"] == 118
    assert result["blood_pressure_diastolic"] == 72
    assert result["blood_pressure_pulse"] == 60


def test_get_health_data_no_blood_pressure_measurement_stays_none():
    """Confirmed against production: a day with no BP entry returns an empty
    measurementSummaries list (not a missing key or null)."""
    client = _make_client()
    client._client = _bp_mock({
        "from": "2026-08-16",
        "until": "2026-08-16",
        "measurementSummaries": [],
        "categoryStats": None,
    })

    result = client.get_health_data(date(2026, 8, 16))

    assert result["blood_pressure_systolic"] is None
    assert result["blood_pressure_diastolic"] is None
    assert result["blood_pressure_pulse"] is None


def test_get_health_data_coerces_blood_pressure_to_int():
    """Garmin's health APIs aren't reliably integer-typed even for whole-number
    values (hydration returns 473.176 in production) — guard against the same
    happening here; the DB column is Integer."""
    client = _make_client()
    client._client = _bp_mock({
        "measurementSummaries": [{
            "measurements": [{
                "systolic": 118.0,
                "diastolic": 72.0,
                "pulse": 60.0,
                "measurementTimestampLocal": "2026-08-21T10:40:36.86",
            }]
        }]
    })

    result = client.get_health_data(date(2026, 8, 21))

    assert result["blood_pressure_systolic"] == 118
    assert isinstance(result["blood_pressure_systolic"], int)
    assert result["blood_pressure_diastolic"] == 72
    assert result["blood_pressure_pulse"] == 60


def test_get_health_data_flattens_measurements_across_multiple_summaries():
    """If Garmin ever groups measurementSummaries into more than one entry for
    a single-day query, the latest reading must still be found across all of
    them, not just summaries[0]."""
    client = _make_client()
    client._client = _bp_mock({
        "measurementSummaries": [
            {
                "measurements": [{
                    "systolic": 140,
                    "diastolic": 90,
                    "pulse": 80,
                    "measurementTimestampLocal": "2026-08-21T08:00:00.00",
                }]
            },
            {
                "measurements": [{
                    "systolic": 118,
                    "diastolic": 72,
                    "pulse": 60,
                    "measurementTimestampLocal": "2026-08-21T20:00:00.00",
                }]
            },
        ]
    })

    result = client.get_health_data(date(2026, 8, 21))

    assert result["blood_pressure_systolic"] == 118


def test_get_health_data_blood_pressure_api_failure_stays_none():
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_stats.return_value = {}
    mock_garmin.get_stress_data.return_value = {}
    mock_garmin.get_body_battery.return_value = []
    mock_garmin.get_spo2_data.return_value = {}
    mock_garmin.get_intensity_minutes_data.return_value = {}
    mock_garmin.get_hydration_data.return_value = {}
    mock_garmin.get_blood_pressure.side_effect = Exception("API error")
    client._client = mock_garmin

    result = client.get_health_data(date(2026, 8, 21))

    assert result["blood_pressure_systolic"] is None
    assert result["blood_pressure_diastolic"] is None
    assert result["blood_pressure_pulse"] is None
