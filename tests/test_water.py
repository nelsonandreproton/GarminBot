"""Tests for B4 hydration tracking: WaterEntry model, repository, and formatter."""

import os
import tempfile
from datetime import date, timedelta

import pytest

from src.database.repository import Repository


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


# ------------------------------------------------------------------ #
# Repository: add_water_entry / get_daily_water                        #
# ------------------------------------------------------------------ #

def test_get_daily_water_empty(repo):
    assert repo.get_daily_water(date.today()) == 0


def test_add_water_entry_and_get_daily(repo):
    today = date.today()
    repo.add_water_entry(today, 250)
    assert repo.get_daily_water(today) == 250


def test_multiple_entries_accumulate(repo):
    today = date.today()
    repo.add_water_entry(today, 250)
    repo.add_water_entry(today, 500)
    repo.add_water_entry(today, 200)
    assert repo.get_daily_water(today) == 950


def test_entries_isolated_by_date(repo):
    today = date.today()
    yesterday = today - timedelta(days=1)
    repo.add_water_entry(today, 300)
    repo.add_water_entry(yesterday, 700)
    assert repo.get_daily_water(today) == 300
    assert repo.get_daily_water(yesterday) == 700


# ------------------------------------------------------------------ #
# Repository: get_weekly_water_avg                                     #
# ------------------------------------------------------------------ #

def test_weekly_water_avg_no_data(repo):
    result = repo.get_weekly_water_avg(date.today())
    assert result is None


def test_weekly_water_avg_single_day(repo):
    end = date.today()
    repo.add_water_entry(end, 2000)
    avg = repo.get_weekly_water_avg(end)
    # 2000 ml logged over 7 days = 2000/7 avg
    assert avg == pytest.approx(2000 / 7, rel=0.01)


def test_weekly_water_avg_multiple_days(repo):
    end = date.today()
    for i in range(7):
        repo.add_water_entry(end - timedelta(days=i), 1000)
    avg = repo.get_weekly_water_avg(end)
    # 7000 ml over 7 days = 1000 avg
    assert avg == pytest.approx(1000.0, rel=0.01)


def test_weekly_water_avg_excludes_outside_range(repo):
    end = date.today()
    repo.add_water_entry(end - timedelta(days=10), 5000)  # outside 7-day window
    repo.add_water_entry(end, 1400)
    avg = repo.get_weekly_water_avg(end)
    # Only 1400 ml in range, divided by 7 days
    assert avg == pytest.approx(1400 / 7, rel=0.01)


# ------------------------------------------------------------------ #
# Formatter: format_daily_summary with water_ml                       #
# ------------------------------------------------------------------ #

def test_format_daily_summary_shows_water():
    from src.telegram.formatters import format_daily_summary
    metrics = {
        "date": date(2026, 2, 15),
        "sleep_hours": 7.5, "sleep_score": 80, "sleep_quality": "Bom",
        "steps": 10000, "active_calories": 400, "resting_calories": 1700,
        "water_ml": 2000,
    }
    text = format_daily_summary(metrics)
    assert "Água" in text
    assert "2.0 L" in text
    assert "2000 ml" in text


def test_format_daily_summary_no_water():
    from src.telegram.formatters import format_daily_summary
    metrics = {
        "date": date(2026, 2, 15),
        "sleep_hours": 7.5, "sleep_score": 80, "sleep_quality": "Bom",
        "steps": 10000, "active_calories": 400, "resting_calories": 1700,
    }
    text = format_daily_summary(metrics)
    assert "Água" not in text


def test_format_daily_summary_zero_water_not_shown():
    from src.telegram.formatters import format_daily_summary
    metrics = {
        "date": date(2026, 2, 15),
        "steps": 10000,
        "water_ml": 0,
    }
    text = format_daily_summary(metrics)
    assert "Água" not in text


# ------------------------------------------------------------------ #
# Formatter: format_weekly_report with water_weekly_avg_ml           #
# ------------------------------------------------------------------ #

def test_format_weekly_report_shows_water_avg():
    from src.telegram.formatters import format_weekly_report
    stats = {
        "start_date": date(2026, 2, 9),
        "end_date": date(2026, 2, 15),
        "sleep_avg_hours": 7.2, "sleep_avg_score": 78,
        "sleep_best_hours": 8.0, "sleep_best_day": date(2026, 2, 10),
        "sleep_worst_hours": 6.5, "sleep_worst_day": date(2026, 2, 12),
        "steps_total": 75000, "steps_avg": 10714,
        "active_calories_total": 3000, "resting_calories_total": 11900,
    }
    text = format_weekly_report(stats, water_weekly_avg_ml=1500)
    assert "Água" in text
    assert "1.5 L/dia" in text


def test_format_weekly_report_no_water():
    from src.telegram.formatters import format_weekly_report
    stats = {
        "start_date": date(2026, 2, 9),
        "end_date": date(2026, 2, 15),
        "sleep_avg_hours": 7.2, "sleep_avg_score": 78,
        "sleep_best_hours": 8.0, "sleep_best_day": date(2026, 2, 10),
        "sleep_worst_hours": 6.5, "sleep_worst_day": date(2026, 2, 12),
        "steps_total": 75000, "steps_avg": 10714,
        "active_calories_total": 3000, "resting_calories_total": 11900,
    }
    text = format_weekly_report(stats, water_weekly_avg_ml=None)
    assert "L/dia" not in text
