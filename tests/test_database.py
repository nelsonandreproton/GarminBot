"""Tests for src/database/repository.py."""

import tempfile
import os
from datetime import date, timedelta

import pytest

from src.database.repository import Repository


@pytest.fixture
def repo():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    r = Repository(db_path)
    r.init_database()
    yield r
    # Dispose the engine to release the file handle before deletion (required on Windows)
    r._engine.dispose()
    try:
        os.unlink(db_path)
    except PermissionError:
        pass  # Windows may still hold the file; not critical for test results


def test_save_and_retrieve_metrics(repo):
    day = date(2026, 2, 13)
    metrics = {
        "sleep_hours": 7.5,
        "sleep_score": 82,
        "sleep_quality": "Excelente",
        "steps": 11000,
        "active_calories": 450,
        "resting_calories": 1700,
        "garmin_sync_success": True,
    }
    repo.save_daily_metrics(day, metrics)
    row = repo.get_metrics_by_date(day)

    assert row is not None
    assert row.sleep_hours == 7.5
    assert row.sleep_score == 82
    assert row.steps == 11000


def test_upsert_updates_existing(repo):
    day = date(2026, 2, 13)
    repo.save_daily_metrics(day, {"steps": 5000, "garmin_sync_success": True})
    repo.save_daily_metrics(day, {"steps": 9999, "garmin_sync_success": True})
    row = repo.get_metrics_by_date(day)
    assert row.steps == 9999


def test_get_metrics_range(repo):
    for i in range(7):
        day = date(2026, 2, 7) + timedelta(days=i)
        repo.save_daily_metrics(day, {"steps": 1000 * (i + 1), "garmin_sync_success": True})
    rows = repo.get_metrics_range(date(2026, 2, 7), date(2026, 2, 13))
    assert len(rows) == 7
    assert rows[0].steps == 1000


def test_get_weekly_stats(repo):
    end = date(2026, 2, 13)
    for i in range(7):
        day = end - timedelta(days=6 - i)
        repo.save_daily_metrics(day, {
            "sleep_hours": 7.0 + i * 0.1,
            "sleep_score": 75,
            "steps": 10000 + i * 100,
            "active_calories": 400,
            "resting_calories": 1700,
            "garmin_sync_success": True,
        })
    stats = repo.get_weekly_stats(end)
    assert stats["days_with_data"] == 7
    assert stats["steps_total"] == sum(10000 + i * 100 for i in range(7))
    assert stats["sleep_avg_score"] == 75


def test_log_sync_and_retrieve(repo):
    repo.log_sync("success")
    repo.log_sync("error", "timeout")
    logs = repo.get_recent_sync_logs(5)
    assert len(logs) == 2
    statuses = {l.status for l in logs}
    assert "success" in statuses
    assert "error" in statuses


def test_count_stored_days(repo):
    assert repo.count_stored_days() == 0
    repo.save_daily_metrics(date(2026, 2, 1), {"garmin_sync_success": True})
    repo.save_daily_metrics(date(2026, 2, 2), {"garmin_sync_success": True})
    assert repo.count_stored_days() == 2


def test_no_data_returns_empty_stats(repo):
    stats = repo.get_weekly_stats(date(2026, 2, 13))
    assert stats == {}


# ------------------------------------------------------------------ #
# Food / nutrition tests                                               #
# ------------------------------------------------------------------ #

def test_save_and_retrieve_food_entries(repo):
    day = date(2026, 2, 13)
    entries = [
        {"name": "ovo", "quantity": 2.0, "unit": "un", "calories": 140.0,
         "protein_g": 12.0, "fat_g": 10.0, "carbs_g": 1.0, "fiber_g": 0.0,
         "source": "openfoodfacts"},
        {"name": "arroz cozido", "quantity": 150.0, "unit": "g", "calories": 195.0,
         "protein_g": 4.0, "fat_g": 0.5, "carbs_g": 42.0, "fiber_g": 1.0,
         "source": "openfoodfacts"},
    ]
    ids = repo.save_food_entries(day, entries)
    assert len(ids) == 2

    rows = repo.get_food_entries(day)
    assert len(rows) == 2
    assert rows[0].name == "ovo"
    assert rows[1].name == "arroz cozido"
    assert rows[1].calories == 195.0


def test_get_daily_nutrition_totals(repo):
    day = date(2026, 2, 13)
    repo.save_food_entries(day, [
        {"name": "item1", "quantity": 1, "unit": "un", "calories": 200.0,
         "protein_g": 10.0, "fat_g": 5.0, "carbs_g": 30.0, "fiber_g": 2.0, "source": "off"},
        {"name": "item2", "quantity": 1, "unit": "un", "calories": 100.0,
         "protein_g": 5.0, "fat_g": 2.0, "carbs_g": 15.0, "fiber_g": 1.0, "source": "off"},
    ])
    totals = repo.get_daily_nutrition(day)
    assert totals["calories"] == 300.0
    assert totals["protein_g"] == 15.0
    assert totals["entry_count"] == 2


def test_get_daily_nutrition_empty_day_returns_zeros(repo):
    totals = repo.get_daily_nutrition(date(2026, 1, 1))
    assert totals["calories"] == 0.0
    assert totals["entry_count"] == 0


def test_delete_last_food_entry(repo):
    day = date(2026, 2, 13)
    repo.save_food_entries(day, [
        {"name": "primeiro", "quantity": 1, "unit": "un", "calories": 100.0,
         "protein_g": 5.0, "fat_g": 3.0, "carbs_g": 10.0, "fiber_g": 0.0, "source": "off"},
        {"name": "segundo", "quantity": 1, "unit": "un", "calories": 200.0,
         "protein_g": 8.0, "fat_g": 5.0, "carbs_g": 20.0, "fiber_g": 1.0, "source": "off"},
    ])
    deleted = repo.delete_last_food_entry(day)
    assert deleted is not None
    assert deleted.name == "segundo"

    remaining = repo.get_food_entries(day)
    assert len(remaining) == 1
    assert remaining[0].name == "primeiro"


def test_delete_last_food_entry_empty_returns_none(repo):
    deleted = repo.delete_last_food_entry(date(2026, 1, 1))
    assert deleted is None


def test_get_weekly_nutrition(repo):
    end = date(2026, 2, 13)
    for i in range(3):
        day = end - timedelta(days=i)
        repo.save_food_entries(day, [
            {"name": "item", "quantity": 1, "unit": "un", "calories": 2000.0,
             "protein_g": 100.0, "fat_g": 70.0, "carbs_g": 250.0, "fiber_g": 25.0,
             "source": "off"},
        ])
    result = repo.get_weekly_nutrition(end)
    assert result["days_with_data"] == 3
    assert result["avg_calories"] == pytest.approx(2000.0, abs=1.0)


def test_set_and_get_macro_goals(repo):
    repo.set_goal("calories", 1750.0)
    repo.set_goal("protein_g", 150.0)
    repo.set_goal("fat_g", 60.0)
    repo.set_goal("carbs_g", 200.0)
    goals = repo.get_goals()
    assert goals["calories"] == 1750.0
    assert goals["protein_g"] == 150.0
    assert goals["fat_g"] == 60.0
    assert goals["carbs_g"] == 200.0
    # Existing defaults still present
    assert goals["steps"] == 10000.0
    assert goals["sleep_hours"] == 7.0
