"""Tests for /sync_atividades: GarminActivity model, repo methods, formatter, and client."""

from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from src.database.repository import Repository
from src.telegram.formatters import (
    format_activities_section,
    format_activity_sync,
    format_daily_summary,
)


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


def _make_activity(activity_id: int, day: date, name: str = "Musculação",
                   type_key: str = "strength_training", duration_min: int = 45,
                   calories: int = 320, distance_km: float | None = None):
    return dict(
        activity_id=activity_id,
        day=day,
        name=name,
        type_key=type_key,
        duration_min=duration_min,
        calories=calories,
        distance_km=distance_km,
    )


# ------------------------------------------------------------------ #
# Repository: upsert_garmin_activity                                  #
# ------------------------------------------------------------------ #

def test_upsert_garmin_activity_insert(repo):
    day = date(2026, 2, 25)
    repo.upsert_garmin_activity(1001, day, "Musculação", "strength_training", 45, 320, None)
    acts = repo.get_garmin_activities_for_date(day)
    assert len(acts) == 1
    assert acts[0].garmin_activity_id == 1001
    assert acts[0].name == "Musculação"
    assert acts[0].duration_min == 45


def test_upsert_garmin_activity_no_duplicate_on_repeat(repo):
    day = date(2026, 2, 25)
    repo.upsert_garmin_activity(1001, day, "Musculação", "strength_training", 45, 320, None)
    repo.upsert_garmin_activity(1001, day, "Musculação", "strength_training", 45, 320, None)
    acts = repo.get_garmin_activities_for_date(day)
    assert len(acts) == 1


def test_upsert_garmin_activity_updates_existing(repo):
    day = date(2026, 2, 25)
    repo.upsert_garmin_activity(1001, day, "Old Name", "strength_training", 40, 300, None)
    repo.upsert_garmin_activity(1001, day, "New Name", "strength_training", 50, 350, None)
    acts = repo.get_garmin_activities_for_date(day)
    assert len(acts) == 1
    assert acts[0].name == "New Name"
    assert acts[0].duration_min == 50


def test_upsert_garmin_activity_multiple_same_day(repo):
    day = date(2026, 2, 25)
    repo.upsert_garmin_activity(1001, day, "Musculação", "strength_training", 45, 320, None)
    repo.upsert_garmin_activity(1002, day, "Corrida", "running", 30, 280, 5.0)
    acts = repo.get_garmin_activities_for_date(day)
    assert len(acts) == 2


def test_get_garmin_activities_for_date_empty(repo):
    assert repo.get_garmin_activities_for_date(date(2026, 2, 25)) == []


def test_get_garmin_activities_for_date_filters_by_day(repo):
    repo.upsert_garmin_activity(1001, date(2026, 2, 25), "A", "running", 30, 200, None)
    repo.upsert_garmin_activity(1002, date(2026, 2, 24), "B", "running", 20, 150, None)
    acts = repo.get_garmin_activities_for_date(date(2026, 2, 25))
    assert len(acts) == 1
    assert acts[0].garmin_activity_id == 1001


# ------------------------------------------------------------------ #
# Repository: get_training_summary_for_llm                            #
# ------------------------------------------------------------------ #

def test_training_summary_empty(repo):
    assert repo.get_training_summary_for_llm() == []


def test_training_summary_manual_only(repo):
    day = date.today() - timedelta(days=1)
    repo.upsert_training_entry(day, "Treino pesado")
    result = repo.get_training_summary_for_llm(days=7)
    assert len(result) == 1
    assert result[0]["description"] == "Treino pesado"
    assert result[0]["date"] == day.isoformat()


def test_training_summary_garmin_only(repo):
    day = date.today() - timedelta(days=1)
    repo.upsert_garmin_activity(1001, day, "Musculação", "strength_training", 45, 320, None)
    result = repo.get_training_summary_for_llm(days=7)
    assert len(result) == 1
    assert "Musculação" in result[0]["description"]
    assert "45min" in result[0]["description"]
    assert "320kcal" in result[0]["description"]


def test_training_summary_combines_manual_and_garmin(repo):
    day = date.today() - timedelta(days=1)
    repo.upsert_training_entry(day, "Nota manual")
    repo.upsert_garmin_activity(1001, day, "Corrida", "running", 30, 250, 5.0)
    result = repo.get_training_summary_for_llm(days=7)
    assert len(result) == 1
    desc = result[0]["description"]
    assert "Nota manual" in desc
    assert "Corrida" in desc
    assert "30min" in desc


def test_training_summary_multiple_garmin_same_day(repo):
    day = date.today() - timedelta(days=1)
    repo.upsert_garmin_activity(1001, day, "Musculação", "strength_training", 45, 320, None)
    repo.upsert_garmin_activity(1002, day, "Corrida", "running", 25, 200, 4.0)
    result = repo.get_training_summary_for_llm(days=7)
    assert len(result) == 1
    desc = result[0]["description"]
    assert "Musculação" in desc
    assert "Corrida" in desc


def test_training_summary_ordered_newest_first(repo):
    older = date.today() - timedelta(days=3)
    newer = date.today() - timedelta(days=1)
    repo.upsert_training_entry(older, "Dia antigo")
    repo.upsert_training_entry(newer, "Dia recente")
    result = repo.get_training_summary_for_llm(days=7)
    assert result[0]["date"] == newer.isoformat()
    assert result[1]["date"] == older.isoformat()


def test_training_summary_respects_days_window(repo):
    old_day = date.today() - timedelta(days=15)
    recent_day = date.today() - timedelta(days=1)
    repo.upsert_garmin_activity(9999, old_day, "Old", "running", 20, 100, None)
    repo.upsert_garmin_activity(1001, recent_day, "Recent", "running", 30, 200, None)
    result = repo.get_training_summary_for_llm(days=7)
    dates = [r["date"] for r in result]
    assert recent_day.isoformat() in dates
    assert old_day.isoformat() not in dates


def test_training_summary_garmin_with_distance(repo):
    day = date.today() - timedelta(days=1)
    repo.upsert_garmin_activity(1001, day, "Corrida", "running", 30, 280, 5.5)
    result = repo.get_training_summary_for_llm(days=7)
    assert "5.5km" in result[0]["description"]


# ------------------------------------------------------------------ #
# GarminClient: get_activities_for_date                               #
# ------------------------------------------------------------------ #

def test_get_activities_for_date_parses_response():
    from src.garmin.client import GarminClient
    client = GarminClient("test@example.com", "password")
    mock_garmin = MagicMock()
    mock_garmin.get_activities_by_date.return_value = [
        {
            "activityId": 12345,
            "activityName": "Morning Strength",
            "activityType": {"typeKey": "strength_training"},
            "duration": 2700.0,   # 45 min
            "calories": 320,
            "distance": None,
        }
    ]
    client._client = mock_garmin

    result = client.get_activities_for_date(date(2026, 2, 25))
    assert len(result) == 1
    assert result[0]["activity_id"] == 12345
    assert result[0]["name"] == "Morning Strength"
    assert result[0]["type_key"] == "strength_training"
    assert result[0]["duration_min"] == 45
    assert result[0]["calories"] == 320
    assert result[0]["distance_km"] is None


def test_get_activities_for_date_with_distance():
    from src.garmin.client import GarminClient
    client = GarminClient("test@example.com", "password")
    mock_garmin = MagicMock()
    mock_garmin.get_activities_by_date.return_value = [
        {
            "activityId": 9999,
            "activityName": "Run",
            "activityType": {"typeKey": "running"},
            "duration": 1800.0,
            "calories": 280,
            "distance": 5000.0,
        }
    ]
    client._client = mock_garmin

    result = client.get_activities_for_date(date(2026, 2, 25))
    assert result[0]["distance_km"] == 5.0


def test_get_activities_for_date_empty_response():
    from src.garmin.client import GarminClient
    client = GarminClient("test@example.com", "password")
    mock_garmin = MagicMock()
    mock_garmin.get_activities_by_date.return_value = []
    client._client = mock_garmin

    result = client.get_activities_for_date(date(2026, 2, 25))
    assert result == []


def test_get_activities_for_date_api_error_returns_empty():
    from src.garmin.client import GarminClient
    client = GarminClient("test@example.com", "password")
    mock_garmin = MagicMock()
    mock_garmin.get_activities_by_date.side_effect = Exception("API error")
    client._client = mock_garmin

    result = client.get_activities_for_date(date(2026, 2, 25))
    assert result == []


def test_get_activities_for_date_skips_entries_without_id():
    from src.garmin.client import GarminClient
    client = GarminClient("test@example.com", "password")
    mock_garmin = MagicMock()
    mock_garmin.get_activities_by_date.return_value = [
        {"activityName": "No ID", "activityType": {"typeKey": "running"}, "duration": 1800},
        {"activityId": 1001, "activityName": "Valid", "activityType": {"typeKey": "running"},
         "duration": 1800, "calories": 200, "distance": None},
    ]
    client._client = mock_garmin

    result = client.get_activities_for_date(date(2026, 2, 25))
    assert len(result) == 1
    assert result[0]["activity_id"] == 1001


def test_get_activities_for_date_parses_heart_rate():
    from src.garmin.client import GarminClient
    client = GarminClient("test@example.com", "password")
    mock_garmin = MagicMock()
    mock_garmin.get_activities_by_date.return_value = [
        {"activityId": 1, "activityName": "Walk", "activityType": {"typeKey": "walking"},
         "duration": 1800, "calories": 150, "distance": 2500.0,
         "averageHR": 110.4, "maxHR": 138.9},
    ]
    client._client = mock_garmin

    result = client.get_activities_for_date(date(2026, 2, 25))
    assert result[0]["avg_hr"] == 110
    assert result[0]["max_hr"] == 139
    assert result[0]["is_indoor"] is False


def test_get_activities_for_date_detects_indoor():
    from src.garmin.client import GarminClient
    client = GarminClient("test@example.com", "password")
    mock_garmin = MagicMock()
    mock_garmin.get_activities_by_date.return_value = [
        {"activityId": 1, "activityName": "Treadmill", "activityType": {"typeKey": "walking"},
         "duration": 1800, "calories": 150, "distance": 2500.0, "isIndoor": True},
    ]
    client._client = mock_garmin

    result = client.get_activities_for_date(date(2026, 2, 25))
    assert result[0]["is_indoor"] is True


def test_get_activities_for_date_aggregates_strength_sets():
    from src.garmin.client import GarminClient
    client = GarminClient("test@example.com", "password")
    mock_garmin = MagicMock()
    mock_garmin.get_activities_by_date.return_value = [
        {"activityId": 7, "activityName": "Gym", "activityType": {"typeKey": "strength_training"},
         "duration": 2700, "calories": 320, "distance": None, "averageHR": 120, "maxHR": 160},
    ]
    mock_garmin.get_activity_exercise_sets.return_value = {
        "exerciseSets": [
            {"setType": "ACTIVE", "repetitionCount": 10, "weight": 20000},  # 20 kg
            {"setType": "REST", "repetitionCount": None, "weight": None},
            {"setType": "ACTIVE", "repetitionCount": 8, "weight": 80000},   # 80 kg
        ]
    }
    client._client = mock_garmin

    result = client.get_activities_for_date(date(2026, 2, 25))
    act = result[0]
    assert act["total_sets"] == 2
    assert act["total_reps"] == 18
    assert act["min_weight_kg"] == 20.0
    assert act["max_weight_kg"] == 80.0
    mock_garmin.get_activity_exercise_sets.assert_called_once_with(7)


def test_get_activities_for_date_strength_sets_error_is_safe():
    from src.garmin.client import GarminClient
    client = GarminClient("test@example.com", "password")
    mock_garmin = MagicMock()
    mock_garmin.get_activities_by_date.return_value = [
        {"activityId": 7, "activityName": "Gym", "activityType": {"typeKey": "strength_training"},
         "duration": 2700, "calories": 320, "distance": None},
    ]
    mock_garmin.get_activity_exercise_sets.side_effect = Exception("boom")
    client._client = mock_garmin

    result = client.get_activities_for_date(date(2026, 2, 25))
    # On error the strength detail is simply omitted — the activity still parses.
    assert result[0]["activity_id"] == 7
    assert "total_sets" not in result[0]
    assert "min_weight_kg" not in result[0]


def test_get_activities_for_date_no_sets_call_for_non_strength():
    from src.garmin.client import GarminClient
    client = GarminClient("test@example.com", "password")
    mock_garmin = MagicMock()
    mock_garmin.get_activities_by_date.return_value = [
        {"activityId": 1, "activityName": "Run", "activityType": {"typeKey": "running"},
         "duration": 1800, "calories": 200, "distance": 5000.0},
    ]
    client._client = mock_garmin

    client.get_activities_for_date(date(2026, 2, 25))
    mock_garmin.get_activity_exercise_sets.assert_not_called()


# ------------------------------------------------------------------ #
# Formatter: format_activity_sync                                     #
# ------------------------------------------------------------------ #

def test_format_activity_sync_empty():
    text = format_activity_sync([], "25/02/2026 (ontem)")
    assert "Sem atividades" in text
    assert "25/02/2026" in text


def test_format_activity_sync_strength():
    acts = [{"name": "Morning Strength", "type_key": "strength_training",
              "duration_min": 45, "calories": 320, "distance_km": None}]
    text = format_activity_sync(acts, "25/02/2026 (ontem)")
    assert "Musculação" in text
    assert "45 min" in text
    assert "320 kcal" in text


def test_format_activity_sync_running_with_distance():
    acts = [{"name": "Evening Run", "type_key": "running",
              "duration_min": 30, "calories": 280, "distance_km": 5.0}]
    text = format_activity_sync(acts, "25/02/2026 (ontem)")
    assert "Corrida" in text
    assert "5.0 km" in text


def test_format_activity_sync_unknown_type_uses_name():
    acts = [{"name": "Custom Workout", "type_key": "unknown_sport",
              "duration_min": 20, "calories": 150, "distance_km": None}]
    text = format_activity_sync(acts, "25/02/2026 (ontem)")
    assert "Custom Workout" in text


def test_format_activity_sync_multiple_activities():
    acts = [
        {"name": "Musculação", "type_key": "strength_training",
         "duration_min": 45, "calories": 320, "distance_km": None},
        {"name": "Corrida", "type_key": "running",
         "duration_min": 25, "calories": 220, "distance_km": 4.0},
    ]
    text = format_activity_sync(acts, "25/02/2026 (hoje)")
    assert "Musculação" in text
    assert "Corrida" in text
    assert "Guardado" in text


def test_format_activity_sync_shows_day_label():
    text = format_activity_sync([], "26/02/2026 (hoje)")
    assert "26/02/2026" in text


# ------------------------------------------------------------------ #
# Formatter: format_activities_section (daily summary block)          #
# ------------------------------------------------------------------ #

def test_activities_section_none_when_empty():
    assert format_activities_section([]) is None
    assert format_activities_section(None) is None


def test_activities_section_walk_shows_distance_time_cals_bpm():
    acts = [{"type_key": "walking", "name": "Caminhada", "distance_km": 3.5,
             "duration_min": 40, "calories": 250, "avg_hr": 110, "max_hr": 140}]
    text = format_activities_section(acts)
    assert "Caminhada" in text
    assert "3.5 km" in text
    assert "40 min" in text
    assert "250 kcal" in text
    assert "110/140 bpm" in text


def test_activities_section_treadmill_labelled_passadeira():
    acts = [{"type_key": "walking", "name": "Walk", "is_indoor": True,
             "distance_km": 2.0, "duration_min": 25, "calories": 180, "avg_hr": 120}]
    text = format_activities_section(acts)
    assert "Passadeira" in text
    assert "Caminhada" not in text


def test_activities_section_strength_shows_rounds_reps_weight():
    acts = [{"type_key": "strength_training", "name": "Gym", "duration_min": 45,
             "calories": 320, "avg_hr": 128, "max_hr": 165,
             "total_sets": 18, "total_reps": 210,
             "min_weight_kg": 20.0, "max_weight_kg": 80.0}]
    text = format_activities_section(acts)
    assert "Musculação" in text
    assert "45 min" in text
    assert "320 kcal" in text
    assert "128/165 bpm" in text
    assert "Rondas: 18" in text
    assert "Reps: 210" in text
    assert "Carga: 20–80 kg" in text


def test_activities_section_strength_single_weight():
    acts = [{"type_key": "strength_training", "name": "Gym", "duration_min": 30,
             "calories": 200, "total_sets": 5, "total_reps": 50,
             "min_weight_kg": 22.5, "max_weight_kg": 22.5}]
    text = format_activities_section(acts)
    assert "Carga: 22.5 kg" in text


def test_daily_summary_includes_activities_section():
    metrics = {"date": date(2026, 2, 25), "steps": 5000,
               "active_calories": 400, "resting_calories": 1500}
    acts = [{"type_key": "running", "name": "Run", "distance_km": 5.0,
             "duration_min": 30, "calories": 280, "avg_hr": 150, "max_hr": 175}]
    text = format_daily_summary(metrics, activities=acts)
    assert "🏋️ *Atividades registadas*" in text
    assert "Corrida" in text
    assert "5.0 km" in text


def test_daily_summary_without_activities_has_no_section():
    metrics = {"date": date(2026, 2, 25), "steps": 5000,
               "active_calories": 400, "resting_calories": 1500}
    text = format_daily_summary(metrics)
    assert "Atividades registadas" not in text


# ------------------------------------------------------------------ #
# Repository: save/round-trip new activity fields                     #
# ------------------------------------------------------------------ #

def test_save_garmin_activities_round_trips_new_fields(repo):
    from src.mcp.formatting import activity_list_to_dicts
    today = date.today()
    acts = [{
        "activity_id": 42, "name": "Gym", "type_key": "strength_training",
        "duration_min": 45, "calories": 320, "distance_km": None,
        "avg_hr": 128, "max_hr": 165, "is_indoor": True,
        "total_sets": 18, "total_reps": 210,
        "min_weight_kg": 20.0, "max_weight_kg": 80.0,
    }]
    repo.save_garmin_activities(today, acts)
    rows = activity_list_to_dicts(repo.get_garmin_activities_for_date(today))
    assert len(rows) == 1
    r = rows[0]
    assert r["avg_hr"] == 128
    assert r["max_hr"] == 165
    assert r["is_indoor"] is True
    assert r["total_sets"] == 18
    assert r["total_reps"] == 210
    assert r["min_weight_kg"] == 20.0
    assert r["max_weight_kg"] == 80.0


# ------------------------------------------------------------------ #
# Repository: get_weekly_training_load                               #
# ------------------------------------------------------------------ #

def test_weekly_training_load_empty(repo):
    result = repo.get_weekly_training_load(date.today())
    assert result == {}


def test_weekly_training_load_single_activity(repo):
    today = date.today()
    repo.upsert_garmin_activity(1, today, "Musculação", "strength_training", 45, 320, None)
    load = repo.get_weekly_training_load(today)
    assert "strength_training" in load
    assert load["strength_training"]["minutes"] == 45
    assert load["strength_training"]["count"] == 1
    assert load["strength_training"]["km"] == 0.0


def test_weekly_training_load_aggregates_same_type(repo):
    today = date.today()
    yesterday = today - timedelta(days=1)
    repo.upsert_garmin_activity(1, today, "Run 1", "running", 30, 250, 5.0)
    repo.upsert_garmin_activity(2, yesterday, "Run 2", "running", 40, 300, 6.0)
    load = repo.get_weekly_training_load(today)
    assert load["running"]["minutes"] == 70
    assert load["running"]["km"] == pytest.approx(11.0)
    assert load["running"]["count"] == 2


def test_weekly_training_load_multiple_types(repo):
    today = date.today()
    repo.upsert_garmin_activity(1, today, "Run", "running", 30, 250, 5.0)
    repo.upsert_garmin_activity(2, today, "Gym", "strength_training", 45, 320, None)
    load = repo.get_weekly_training_load(today)
    assert "running" in load
    assert "strength_training" in load


def test_weekly_training_load_excludes_outside_window(repo):
    today = date.today()
    repo.upsert_garmin_activity(1, today - timedelta(days=10), "Old", "running", 30, 200, 4.0)
    repo.upsert_garmin_activity(2, today, "Recent", "running", 20, 150, 3.0)
    load = repo.get_weekly_training_load(today)
    assert load["running"]["minutes"] == 20
    assert load["running"]["count"] == 1


def test_weekly_training_load_none_type_key_becomes_other(repo):
    today = date.today()
    repo.upsert_garmin_activity(1, today, "Unknown sport", None, 25, 150, None)
    load = repo.get_weekly_training_load(today)
    assert "other" in load


# ------------------------------------------------------------------ #
# Formatter: format_weekly_training_load                             #
# ------------------------------------------------------------------ #

def test_format_weekly_training_load_empty():
    from src.telegram.formatters import format_weekly_training_load
    assert format_weekly_training_load({}) == ""


def test_format_weekly_training_load_running():
    from src.telegram.formatters import format_weekly_training_load
    load = {"running": {"minutes": 90, "km": 14.5, "count": 3}}
    text = format_weekly_training_load(load)
    assert "Corrida" in text
    assert "90min" in text
    assert "14.5 km" in text
    assert "3× sessões" in text


def test_format_weekly_training_load_strength_no_distance():
    from src.telegram.formatters import format_weekly_training_load
    load = {"strength_training": {"minutes": 45, "km": 0.0, "count": 1}}
    text = format_weekly_training_load(load)
    assert "Musculação" in text
    assert "45min" in text
    assert "km" not in text
    assert "1× sessão" in text


def test_format_weekly_training_load_total_shown():
    from src.telegram.formatters import format_weekly_training_load
    load = {
        "running": {"minutes": 30, "km": 5.0, "count": 1},
        "strength_training": {"minutes": 45, "km": 0.0, "count": 1},
    }
    text = format_weekly_training_load(load)
    assert "Total: 75min" in text
    assert "5.0 km" in text
