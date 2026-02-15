"""Tests for weight tracking feature across all layers."""

import tempfile
import os
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.database.repository import Repository
from src.garmin.client import (
    ActivityData,
    DailySummary,
    GarminClient,
    SleepData,
)
from src.telegram.formatters import (
    format_daily_summary,
    format_goals,
    format_weekly_report,
    format_weekly_weight,
    format_weight_status,
)
from src.utils.insights import generate_insights


# ------------------------------------------------------------------ #
# Fixtures                                                              #
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


def _make_client() -> GarminClient:
    return GarminClient("test@example.com", "password")


def _make_row(day: date, steps=None, sleep_hours=None, weight_kg=None):
    row = MagicMock()
    row.date = day
    row.steps = steps
    row.sleep_hours = sleep_hours
    row.weight_kg = weight_kg
    return row


# ------------------------------------------------------------------ #
# Database: migration + CRUD                                           #
# ------------------------------------------------------------------ #

def test_migration_adds_weight_column(repo):
    """weight_kg column should exist after init_database."""
    day = date(2026, 2, 15)
    repo.save_daily_metrics(day, {"weight_kg": 78.5, "garmin_sync_success": True})
    row = repo.get_metrics_by_date(day)
    assert row is not None
    assert row.weight_kg == 78.5


def test_save_manual_weight_new_day(repo):
    """save_manual_weight creates a row if none exists."""
    day = date(2026, 2, 15)
    repo.save_manual_weight(day, 78.5)
    row = repo.get_metrics_by_date(day)
    assert row is not None
    assert row.weight_kg == 78.5
    assert row.garmin_sync_success is False


def test_save_manual_weight_existing_day(repo):
    """save_manual_weight updates existing row without overwriting other data."""
    day = date(2026, 2, 15)
    repo.save_daily_metrics(day, {"steps": 10000, "garmin_sync_success": True})
    repo.save_manual_weight(day, 79.0)
    row = repo.get_metrics_by_date(day)
    assert row.weight_kg == 79.0
    assert row.steps == 10000  # preserved


def test_get_latest_weight_empty(repo):
    weight, day = repo.get_latest_weight()
    assert weight is None
    assert day is None


def test_get_latest_weight(repo):
    repo.save_manual_weight(date(2026, 2, 10), 80.0)
    repo.save_manual_weight(date(2026, 2, 13), 79.0)
    repo.save_manual_weight(date(2026, 2, 15), 78.5)
    weight, day = repo.get_latest_weight()
    assert weight == 78.5
    assert day == date(2026, 2, 15)


def test_get_latest_weight_with_before_date(repo):
    repo.save_manual_weight(date(2026, 2, 10), 80.0)
    repo.save_manual_weight(date(2026, 2, 15), 78.5)
    weight, day = repo.get_latest_weight(before_date=date(2026, 2, 12))
    assert weight == 80.0
    assert day == date(2026, 2, 10)


def test_get_weekly_weight_stats_empty(repo):
    stats = repo.get_weekly_weight_stats(date(2026, 2, 15))
    assert stats == {}


def test_get_weekly_weight_stats(repo):
    # Previous week
    repo.save_manual_weight(date(2026, 2, 5), 80.0)
    # Current week
    repo.save_manual_weight(date(2026, 2, 10), 79.5)
    repo.save_manual_weight(date(2026, 2, 12), 79.0)
    repo.save_manual_weight(date(2026, 2, 15), 78.5)

    stats = repo.get_weekly_weight_stats(date(2026, 2, 15))
    assert stats["current_weight"] == 78.5
    assert stats["current_date"] == date(2026, 2, 15)
    assert stats["prev_weight"] == 80.0
    assert stats["delta"] == -1.5
    assert stats["min_weight"] == 78.5
    assert stats["max_weight"] == 79.5
    assert stats["entries_count"] == 3


def test_get_weekly_weight_stats_no_previous_week(repo):
    repo.save_manual_weight(date(2026, 2, 12), 79.0)
    repo.save_manual_weight(date(2026, 2, 15), 78.5)

    stats = repo.get_weekly_weight_stats(date(2026, 2, 15))
    assert stats["current_weight"] == 78.5
    assert stats["delta"] is None  # no previous week data


# ------------------------------------------------------------------ #
# Garmin client: get_weight_data                                       #
# ------------------------------------------------------------------ #

def test_get_weight_data_parses_response():
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_body_composition.return_value = {
        "dailyWeightSummaries": [
            {
                "allWeightMetrics": [
                    {"weight": 78500}  # 78.5 kg in grams
                ]
            }
        ]
    }
    client._client = mock_garmin

    result = client.get_weight_data(date(2026, 2, 15))
    assert result == 78.5
    mock_garmin.get_body_composition.assert_called_once_with("2026-02-15")


def test_get_weight_data_empty_response():
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_body_composition.return_value = {}
    client._client = mock_garmin

    result = client.get_weight_data(date(2026, 2, 15))
    assert result is None


def test_get_weight_data_no_summaries():
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_body_composition.return_value = {"dailyWeightSummaries": []}
    client._client = mock_garmin

    result = client.get_weight_data(date(2026, 2, 15))
    assert result is None


def test_get_weight_data_api_error():
    client = _make_client()
    mock_garmin = MagicMock()
    mock_garmin.get_body_composition.side_effect = Exception("API error")
    client._client = mock_garmin

    result = client.get_weight_data(date(2026, 2, 15))
    assert result is None


def test_to_metrics_dict_includes_weight():
    client = _make_client()
    summary = DailySummary(
        date=date(2026, 2, 15),
        sleep=SleepData(hours=7.5, score=82, quality="Excelente"),
        activity=ActivityData(steps=10000, active_calories=400, resting_calories=1700),
        weight_kg=78.5,
    )
    d = client.to_metrics_dict(summary)
    assert d["weight_kg"] == 78.5


def test_to_metrics_dict_weight_none():
    client = _make_client()
    summary = DailySummary(
        date=date(2026, 2, 15),
        sleep=SleepData(hours=7.5, score=82, quality="Excelente"),
        activity=ActivityData(steps=10000, active_calories=400, resting_calories=1700),
    )
    d = client.to_metrics_dict(summary)
    assert d["weight_kg"] is None


# ------------------------------------------------------------------ #
# Formatters: daily, weekly, weight status                             #
# ------------------------------------------------------------------ #

def test_format_daily_summary_with_weight():
    metrics = {
        "date": date(2026, 2, 15),
        "sleep_hours": 7.5, "sleep_score": 82, "sleep_quality": "Excelente",
        "steps": 12000, "active_calories": 450, "resting_calories": 1700,
        "resting_heart_rate": 58, "weight_kg": 78.5,
    }
    text = format_daily_summary(metrics)
    assert "Peso: 78.5 kg" in text
    assert "Saúde" in text


def test_format_daily_summary_without_weight():
    metrics = {
        "date": date(2026, 2, 15),
        "sleep_hours": 7.5, "sleep_score": 82, "sleep_quality": "Excelente",
        "steps": 12000, "active_calories": 450, "resting_calories": 1700,
    }
    text = format_daily_summary(metrics)
    assert "Peso" not in text


def test_format_weekly_weight():
    stats = {
        "current_weight": 78.5,
        "current_date": date(2026, 2, 13),  # Friday
        "delta": -0.3,
        "min_weight": 78.2,
        "max_weight": 79.1,
    }
    text = format_weekly_weight(stats)
    assert "78.5 kg" in text
    assert "Sexta" in text
    assert "-0.3 kg" in text
    assert "78.2" in text
    assert "79.1" in text


def test_format_weekly_weight_no_delta():
    stats = {
        "current_weight": 78.5,
        "current_date": date(2026, 2, 14),
        "delta": None,
        "min_weight": 78.5,
        "max_weight": 78.5,
    }
    text = format_weekly_weight(stats)
    assert "78.5 kg" in text
    assert "semana passada" not in text


def test_format_weekly_report_with_weight():
    stats = {
        "start_date": date(2026, 2, 9),
        "end_date": date(2026, 2, 15),
        "sleep_avg_hours": 7.2, "sleep_avg_score": 78,
        "sleep_best_hours": 8.0, "sleep_best_day": date(2026, 2, 10),
        "sleep_worst_hours": 6.5, "sleep_worst_day": date(2026, 2, 12),
        "steps_total": 75000, "steps_avg": 10714,
        "active_calories_total": 3000, "resting_calories_total": 11900,
    }
    weight_stats = {
        "current_weight": 78.5,
        "current_date": date(2026, 2, 15),
        "delta": -0.5,
        "min_weight": 78.2,
        "max_weight": 79.0,
    }
    text = format_weekly_report(stats, weight_stats=weight_stats)
    assert "Peso" in text
    assert "78.5 kg" in text
    assert "-0.5 kg" in text


def test_format_weekly_report_without_weight():
    stats = {
        "start_date": date(2026, 2, 9),
        "end_date": date(2026, 2, 15),
        "sleep_avg_hours": 7.2, "sleep_avg_score": 78,
        "sleep_best_hours": 8.0, "sleep_best_day": date(2026, 2, 10),
        "sleep_worst_hours": 6.5, "sleep_worst_day": date(2026, 2, 12),
        "steps_total": 75000, "steps_avg": 10714,
        "active_calories_total": 3000, "resting_calories_total": 11900,
    }
    text = format_weekly_report(stats)
    assert "Peso" not in text


def test_format_weight_status_no_data():
    text = format_weight_status(None, None)
    assert "Sem registos" in text


def test_format_weight_status_with_data():
    text = format_weight_status(78.5, date(2026, 2, 15))
    assert "78.5 kg" in text
    assert "15/02" in text


def test_format_weight_status_with_goal():
    text = format_weight_status(
        78.5, date(2026, 2, 15),
        goals={"weight_kg": 75.0},
    )
    assert "Objetivo: 75.0 kg" in text
    assert "faltam 3.5 kg" in text


def test_format_weight_status_goal_reached():
    text = format_weight_status(
        75.0, date(2026, 2, 15),
        goals={"weight_kg": 75.0},
    )
    assert "atingido" in text


def test_format_weight_status_with_weekly_stats():
    stats = {
        "prev_weight": 79.0,
        "delta": -0.5,
        "entries_count": 3,
    }
    text = format_weight_status(78.5, date(2026, 2, 15), weight_stats=stats)
    assert "79.0 kg" in text
    assert "-0.5 kg" in text
    assert "3" in text


def test_format_goals_with_weight():
    goals = {"steps": 10000.0, "sleep_hours": 7.0, "weight_kg": 75.0}
    text = format_goals(goals)
    assert "Peso alvo: 75.0 kg" in text


def test_format_goals_without_weight():
    goals = {"steps": 10000.0, "sleep_hours": 7.0}
    text = format_goals(goals)
    assert "Peso" not in text


# ------------------------------------------------------------------ #
# Insights: weight trends                                              #
# ------------------------------------------------------------------ #

def test_insights_weight_loss_trend():
    rows = [
        _make_row(date(2026, 2, 1), steps=10000, weight_kg=80.0),
        _make_row(date(2026, 2, 7), steps=10000, weight_kg=79.5),
        _make_row(date(2026, 2, 14), steps=10000, weight_kg=79.0),
    ]
    insights = generate_insights(rows)
    assert any("Peso" in i or "⚖️" in i for i in insights)
    assert any("-1.0 kg" in i for i in insights)


def test_insights_weight_gain_trend():
    rows = [
        _make_row(date(2026, 2, 1), steps=10000, weight_kg=78.0),
        _make_row(date(2026, 2, 14), steps=10000, weight_kg=79.5),
    ]
    insights = generate_insights(rows)
    assert any("+1.5 kg" in i for i in insights)


def test_insights_weight_near_goal():
    rows = [
        _make_row(date(2026, 2, 1), steps=10000, weight_kg=75.3),
        _make_row(date(2026, 2, 14), steps=10000, weight_kg=75.2),
    ]
    goals = {"steps": 10000, "sleep_hours": 7.0, "weight_kg": 75.0}
    insights = generate_insights(rows, goals=goals)
    assert any("próximo" in i or "objetivo" in i.lower() for i in insights)


def test_insights_weight_below_goal():
    rows = [
        _make_row(date(2026, 2, 1), steps=10000, weight_kg=74.0),
        _make_row(date(2026, 2, 14), steps=10000, weight_kg=74.5),
    ]
    goals = {"steps": 10000, "sleep_hours": 7.0, "weight_kg": 75.0}
    insights = generate_insights(rows, goals=goals)
    assert any("abaixo" in i for i in insights)


def test_insights_no_weight_data():
    rows = [
        _make_row(date(2026, 2, 1), steps=10000),
        _make_row(date(2026, 2, 14), steps=10000),
    ]
    insights = generate_insights(rows)
    # No weight insights should appear
    assert not any("⚖️" in i for i in insights)


def test_insights_single_weight_point():
    rows = [
        _make_row(date(2026, 2, 14), steps=10000, weight_kg=78.0),
    ]
    insights = generate_insights(rows)
    # Need at least 2 data points for trend
    assert not any("⚖️" in i for i in insights)


def test_insights_weight_stable():
    """Small weight change (< 0.3 kg) should not trigger insight."""
    rows = [
        _make_row(date(2026, 2, 1), steps=10000, weight_kg=78.0),
        _make_row(date(2026, 2, 14), steps=10000, weight_kg=78.1),
    ]
    insights = generate_insights(rows)
    assert not any("⚖️" in i for i in insights)


# ------------------------------------------------------------------ #
# Charts: weight subplot                                               #
# ------------------------------------------------------------------ #

def test_weekly_chart_with_weight():
    """Chart generation should not crash with weight data."""
    from src.utils.charts import generate_weekly_chart
    rows = []
    for i in range(7):
        row = MagicMock()
        row.date = date(2026, 2, 9) + timedelta(days=i)
        row.steps = 10000
        row.sleep_hours = 7.5
        row.weight_kg = 78.0 + i * 0.1 if i % 2 == 0 else None
        rows.append(row)
    result = generate_weekly_chart(rows)
    assert result is not None
    assert len(result) > 0


def test_weekly_chart_without_weight():
    """Chart generation should work fine without weight data."""
    from src.utils.charts import generate_weekly_chart
    rows = []
    for i in range(7):
        row = MagicMock()
        row.date = date(2026, 2, 9) + timedelta(days=i)
        row.steps = 10000
        row.sleep_hours = 7.5
        row.weight_kg = None
        rows.append(row)
    result = generate_weekly_chart(rows)
    assert result is not None
    assert len(result) > 0


def test_weekly_chart_with_weight_goal():
    """Chart with weight goal line should not crash."""
    from src.utils.charts import generate_weekly_chart
    rows = []
    for i in range(7):
        row = MagicMock()
        row.date = date(2026, 2, 9) + timedelta(days=i)
        row.steps = 10000
        row.sleep_hours = 7.5
        row.weight_kg = 78.5
        rows.append(row)
    goals = {"steps": 10000, "sleep_hours": 7.0, "weight_kg": 75.0}
    result = generate_weekly_chart(rows, goals=goals)
    assert result is not None
    assert len(result) > 0
