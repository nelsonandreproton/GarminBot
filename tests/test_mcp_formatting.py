"""Tests for src/mcp/formatting.py — pure JSON-safe converter functions.
Written before implementation (TDD).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from src.database.models import (
    DailyMetrics,
    FoodEntry,
    GarminActivity,
    TrainingEntry,
)
from src.mcp.formatting import (
    activity_list_to_dicts,
    activity_to_dict,
    food_entry_list_to_dicts,
    food_entry_to_dict,
    metrics_list_to_dicts,
    metrics_to_dict,
    stats_dict,
    training_entry_list_to_dicts,
    training_entry_to_dict,
    weight_records_to_list,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_metrics(**kwargs) -> DailyMetrics:
    defaults = dict(
        date=date(2024, 6, 1),
        sleep_hours=7.5,
        sleep_score=80,
        sleep_quality="good",
        sleep_deep_min=90,
        sleep_light_min=200,
        sleep_rem_min=120,
        sleep_awake_min=10,
        steps=9000,
        active_calories=400,
        resting_calories=1800,
        total_calories=2200,
        floors_ascended=5,
        intensity_moderate_min=20,
        intensity_vigorous_min=10,
        resting_heart_rate=58,
        avg_stress=30,
        body_battery_high=85,
        body_battery_low=20,
        spo2_avg=97.5,
        weight_kg=80.5,
        synced_at=datetime(2024, 6, 1, 8, 0, 0, tzinfo=UTC),
        garmin_sync_success=True,
    )
    defaults.update(kwargs)
    return DailyMetrics(**defaults)


def _make_activity(**kwargs) -> GarminActivity:
    defaults = dict(
        garmin_activity_id=12345,
        date=date(2024, 6, 1),
        name="Morning Run",
        type_key="running",
        duration_min=30,
        calories=300,
        distance_km=5.0,
        synced_at=datetime(2024, 6, 1, 9, 0, 0, tzinfo=UTC),
    )
    defaults.update(kwargs)
    return GarminActivity(**defaults)


def _make_food_entry(**kwargs) -> FoodEntry:
    defaults = dict(
        date=date(2024, 6, 1),
        name="Banana",
        quantity=1.0,
        unit="un",
        calories=89.0,
        protein_g=1.1,
        fat_g=0.3,
        carbs_g=23.0,
        fiber_g=2.6,
        source="manual",
        barcode=None,
        created_at=datetime(2024, 6, 1, 7, 30, 0, tzinfo=UTC),
    )
    defaults.update(kwargs)
    return FoodEntry(**defaults)


def _make_training_entry(**kwargs) -> TrainingEntry:
    defaults = dict(
        date=date(2024, 6, 1),
        description="Peito + Triceps",
        created_at=datetime(2024, 6, 1, 18, 0, 0, tzinfo=UTC),
    )
    defaults.update(kwargs)
    return TrainingEntry(**defaults)


# ---------------------------------------------------------------------------
# metrics_to_dict
# ---------------------------------------------------------------------------

class TestMetricsToDict:
    def test_returns_none_for_none(self):
        assert metrics_to_dict(None) is None

    def test_date_is_iso_string(self):
        row = _make_metrics()
        result = metrics_to_dict(row)
        assert result["date"] == "2024-06-01"
        assert isinstance(result["date"], str)

    def test_synced_at_is_iso_string(self):
        row = _make_metrics()
        result = metrics_to_dict(row)
        assert isinstance(result["synced_at"], str)
        assert "2024-06-01" in result["synced_at"]

    def test_numeric_values_preserved(self):
        row = _make_metrics()
        result = metrics_to_dict(row)
        assert result["steps"] == 9000
        assert result["sleep_hours"] == 7.5
        assert result["weight_kg"] == 80.5

    def test_json_serializable(self):
        row = _make_metrics()
        result = metrics_to_dict(row)
        dumped = json.dumps(result)
        assert json.loads(dumped)["steps"] == 9000

    def test_none_values_preserved(self):
        row = _make_metrics(sleep_hours=None, steps=None)
        result = metrics_to_dict(row)
        assert result["sleep_hours"] is None
        assert result["steps"] is None


# ---------------------------------------------------------------------------
# metrics_list_to_dicts
# ---------------------------------------------------------------------------

class TestMetricsListToDicts:
    def test_empty_list(self):
        assert metrics_list_to_dicts([]) == []

    def test_multiple_rows(self):
        rows = [_make_metrics(date=date(2024, 6, d)) for d in [1, 2, 3]]
        result = metrics_list_to_dicts(rows)
        assert len(result) == 3
        assert result[0]["date"] == "2024-06-01"
        assert result[2]["date"] == "2024-06-03"

    def test_json_serializable(self):
        rows = [_make_metrics()]
        json.dumps(metrics_list_to_dicts(rows))  # must not raise


# ---------------------------------------------------------------------------
# stats_dict
# ---------------------------------------------------------------------------

class TestStatsDict:
    def test_converts_date_values_to_iso(self):
        d = {"start_date": date(2024, 6, 1), "end_date": date(2024, 6, 7), "days_with_data": 5}
        result = stats_dict(d)
        assert result["start_date"] == "2024-06-01"
        assert result["end_date"] == "2024-06-07"
        assert result["days_with_data"] == 5

    def test_converts_datetime_values_to_iso(self):
        dt = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        d = {"updated_at": dt, "count": 3}
        result = stats_dict(d)
        assert isinstance(result["updated_at"], str)
        assert "2024-06-01" in result["updated_at"]
        assert result["count"] == 3

    def test_none_values_preserved(self):
        d = {"sleep_best_day": None, "steps_avg": 9000}
        result = stats_dict(d)
        assert result["sleep_best_day"] is None
        assert result["steps_avg"] == 9000

    def test_empty_dict(self):
        assert stats_dict({}) == {}

    def test_does_not_mutate_input(self):
        original_date = date(2024, 6, 1)
        d = {"date": original_date}
        stats_dict(d)
        assert d["date"] is original_date  # input unchanged

    def test_json_serializable_with_dates(self):
        d = {
            "start_date": date(2024, 6, 1),
            "end_date": date(2024, 6, 7),
            "sleep_avg_hours": 7.2,
            "steps_total": 63000,
            "sleep_best_day": date(2024, 6, 3),
            "sleep_worst_day": None,
        }
        json.dumps(stats_dict(d))  # must not raise


# ---------------------------------------------------------------------------
# weight_records_to_list
# ---------------------------------------------------------------------------

class TestWeightRecordsToList:
    def test_empty(self):
        assert weight_records_to_list([]) == []

    def test_converts_tuples_to_dicts(self):
        pairs = [(date(2024, 6, 1), 80.5), (date(2024, 6, 2), 80.2)]
        result = weight_records_to_list(pairs)
        assert len(result) == 2
        assert result[0] == {"date": "2024-06-01", "weight_kg": 80.5}
        assert result[1] == {"date": "2024-06-02", "weight_kg": 80.2}

    def test_json_serializable(self):
        pairs = [(date(2024, 6, 1), 80.5)]
        json.dumps(weight_records_to_list(pairs))  # must not raise


# ---------------------------------------------------------------------------
# activity_to_dict / activity_list_to_dicts
# ---------------------------------------------------------------------------

class TestActivityToDict:
    def test_key_fields_present(self):
        row = _make_activity()
        result = activity_to_dict(row)
        assert result["garmin_activity_id"] == 12345
        assert result["name"] == "Morning Run"
        assert result["type_key"] == "running"
        assert result["duration_min"] == 30
        assert result["calories"] == 300
        assert result["distance_km"] == 5.0

    def test_date_is_iso_string(self):
        row = _make_activity()
        result = activity_to_dict(row)
        assert result["date"] == "2024-06-01"

    def test_synced_at_is_iso_string(self):
        row = _make_activity()
        result = activity_to_dict(row)
        assert isinstance(result["synced_at"], str)

    def test_json_serializable(self):
        row = _make_activity()
        json.dumps(activity_to_dict(row))  # must not raise

    def test_list_empty(self):
        assert activity_list_to_dicts([]) == []

    def test_list_multiple(self):
        rows = [_make_activity(garmin_activity_id=i) for i in [1, 2]]
        result = activity_list_to_dicts(rows)
        assert len(result) == 2
        assert result[0]["garmin_activity_id"] == 1

    def test_list_json_serializable(self):
        rows = [_make_activity()]
        json.dumps(activity_list_to_dicts(rows))  # must not raise


# ---------------------------------------------------------------------------
# training_entry_to_dict / training_entry_list_to_dicts
# ---------------------------------------------------------------------------

class TestTrainingEntryToDict:
    def test_key_fields_present(self):
        row = _make_training_entry()
        result = training_entry_to_dict(row)
        assert result["description"] == "Peito + Triceps"
        assert result["date"] == "2024-06-01"

    def test_created_at_is_iso_string(self):
        row = _make_training_entry()
        result = training_entry_to_dict(row)
        assert isinstance(result["created_at"], str)

    def test_json_serializable(self):
        row = _make_training_entry()
        json.dumps(training_entry_to_dict(row))  # must not raise

    def test_list_empty(self):
        assert training_entry_list_to_dicts([]) == []

    def test_list_multiple(self):
        rows = [_make_training_entry(description=f"Workout {i}") for i in [1, 2]]
        result = training_entry_list_to_dicts(rows)
        assert len(result) == 2

    def test_list_json_serializable(self):
        rows = [_make_training_entry()]
        json.dumps(training_entry_list_to_dicts(rows))  # must not raise


# ---------------------------------------------------------------------------
# food_entry_to_dict / food_entry_list_to_dicts
# ---------------------------------------------------------------------------

class TestFoodEntryToDict:
    def test_key_fields_present(self):
        row = _make_food_entry()
        result = food_entry_to_dict(row)
        assert result["name"] == "Banana"
        assert result["calories"] == 89.0
        assert result["protein_g"] == 1.1
        assert result["carbs_g"] == 23.0
        assert result["source"] == "manual"
        assert result["quantity"] == 1.0
        assert result["unit"] == "un"

    def test_date_is_iso_string(self):
        row = _make_food_entry()
        result = food_entry_to_dict(row)
        assert result["date"] == "2024-06-01"

    def test_created_at_is_iso_string(self):
        row = _make_food_entry()
        result = food_entry_to_dict(row)
        assert isinstance(result["created_at"], str)

    def test_barcode_none_preserved(self):
        row = _make_food_entry(barcode=None)
        result = food_entry_to_dict(row)
        assert result["barcode"] is None

    def test_json_serializable(self):
        row = _make_food_entry()
        json.dumps(food_entry_to_dict(row))  # must not raise

    def test_list_empty(self):
        assert food_entry_list_to_dicts([]) == []

    def test_list_multiple(self):
        rows = [_make_food_entry(name=f"Food {i}") for i in [1, 2, 3]]
        result = food_entry_list_to_dicts(rows)
        assert len(result) == 3
        assert result[0]["name"] == "Food 1"

    def test_list_json_serializable(self):
        rows = [_make_food_entry()]
        json.dumps(food_entry_list_to_dicts(rows))  # must not raise
