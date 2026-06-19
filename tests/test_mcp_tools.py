"""Tests for src/mcp/tools.py and MCP server registration.

TDD: these tests were written before implementation.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from src.database.repository import Repository
from src.mcp.tools import (
    get_activities,
    get_daily_metrics,
    get_deficit,
    get_goals,
    get_metrics_range,
    get_monthly_stats,
    get_nutrition,
    get_nutrition_trend,
    get_training_load,
    get_weight_trend,
    get_weekly_stats,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    """One shared repo seeded with known data for the whole module."""
    db_path = str(tmp_path_factory.mktemp("mcp_tools") / "test.db")
    r = Repository(db_path)
    r.init_database()
    _seed(r)
    yield r
    r._engine.dispose()


def _seed(r: Repository) -> None:
    """Seed representative data across several days."""
    today = date.today()
    for i in range(7):
        day = today - timedelta(days=i)
        r.save_daily_metrics(day, {
            "sleep_hours": 7.0 + i * 0.1,
            "sleep_score": 80,
            "sleep_quality": "good",
            "sleep_deep_min": 90,
            "sleep_light_min": 200,
            "sleep_rem_min": 110,
            "sleep_awake_min": 10,
            "steps": 8000 + i * 100,
            "active_calories": 400 + i * 10,
            "resting_calories": 1800,
            "total_calories": 2400 + i * 10,  # deliberately != active+resting (sum is 2200+i*10)
            "floors_ascended": 5,
            "intensity_moderate_min": 20,
            "intensity_vigorous_min": 10,
            "resting_heart_rate": 58,
            "avg_stress": 30,
            "body_battery_high": 85,
            "body_battery_low": 20,
            "spo2_avg": 97.5,
            "weight_kg": 80.5 - i * 0.1,
            "garmin_sync_success": True,
        })

    # Food entries for today
    r.save_food_entries(today, [
        {
            "name": "Oatmeal",
            "quantity": 1.0,
            "unit": "bowl",
            "calories": 350.0,
            "protein_g": 10.0,
            "fat_g": 5.0,
            "carbs_g": 60.0,
            "fiber_g": 8.0,
            "source": "manual",
        },
        {
            "name": "Banana",
            "quantity": 1.0,
            "unit": "un",
            "calories": 90.0,
            "protein_g": 1.0,
            "fat_g": 0.3,
            "carbs_g": 23.0,
            "fiber_g": 2.5,
            "source": "manual",
        },
    ])

    # Garmin activities for today and yesterday
    r.upsert_garmin_activity(
        activity_id=1001,
        day=today,
        name="Morning Run",
        type_key="running",
        duration_min=35,
        calories=320,
        distance_km=5.2,
    )
    r.upsert_garmin_activity(
        activity_id=1002,
        day=today - timedelta(days=1),
        name="Cycling",
        type_key="cycling",
        duration_min=60,
        calories=500,
        distance_km=20.0,
    )

    # Training entry
    r.upsert_training_entry(today, "Peito + Triceps 3x10")

    # Water
    r.add_water_entry(today, 500)
    r.add_water_entry(today, 300)

    # Custom goal
    r.set_goal("steps", 9000.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def assert_json_safe(value):
    """Assert that json.dumps succeeds on value (no date objects leaking)."""
    json.dumps(value)  # raises TypeError if not JSON-safe


# ---------------------------------------------------------------------------
# get_daily_metrics
# ---------------------------------------------------------------------------

class TestGetDailyMetrics:
    def test_returns_dict_for_seeded_day(self, repo):
        today = date.today()
        result = get_daily_metrics(repo, today)
        assert result is not None
        assert result["date"] == today.isoformat()
        assert result["steps"] == 8000

    def test_defaults_to_today(self, repo):
        result = get_daily_metrics(repo)
        assert result is not None
        assert result["date"] == date.today().isoformat()

    def test_returns_none_for_missing_day(self, repo):
        future = date.today() + timedelta(days=365)
        result = get_daily_metrics(repo, future)
        assert result is None

    def test_json_safe(self, repo):
        result = get_daily_metrics(repo, date.today())
        assert_json_safe(result)


# ---------------------------------------------------------------------------
# get_metrics_range
# ---------------------------------------------------------------------------

class TestGetMetricsRange:
    def test_returns_list_for_range(self, repo):
        today = date.today()
        result = get_metrics_range(repo, today - timedelta(days=2), today)
        assert isinstance(result, list)
        assert len(result) == 3

    def test_empty_for_future_range(self, repo):
        future = date.today() + timedelta(days=365)
        result = get_metrics_range(repo, future, future + timedelta(days=3))
        assert result == []

    def test_json_safe(self, repo):
        today = date.today()
        result = get_metrics_range(repo, today - timedelta(days=2), today)
        assert_json_safe(result)


# ---------------------------------------------------------------------------
# get_weekly_stats
# ---------------------------------------------------------------------------

class TestGetWeeklyStats:
    def test_returns_dict_with_expected_keys(self, repo):
        result = get_weekly_stats(repo)
        assert isinstance(result, dict)
        assert "steps_avg" in result
        assert "sleep_avg_hours" in result
        assert "days_with_data" in result

    def test_json_safe(self, repo):
        result = get_weekly_stats(repo)
        assert_json_safe(result)

    def test_empty_for_no_data(self, repo):
        future = date.today() + timedelta(days=365)
        result = get_weekly_stats(repo, end_date=future)
        assert result == {}


# ---------------------------------------------------------------------------
# get_monthly_stats
# ---------------------------------------------------------------------------

class TestGetMonthlyStats:
    def test_returns_dict_with_expected_keys(self, repo):
        result = get_monthly_stats(repo)
        assert isinstance(result, dict)
        assert "steps_avg" in result
        assert "sleep_avg_hours" in result

    def test_json_safe(self, repo):
        result = get_monthly_stats(repo)
        assert_json_safe(result)

    def test_empty_for_no_data(self, repo):
        future = date.today() + timedelta(days=365)
        result = get_monthly_stats(repo, end_date=future)
        assert result == {}


# ---------------------------------------------------------------------------
# get_weight_trend
# ---------------------------------------------------------------------------

class TestGetWeightTrend:
    def test_returns_records_and_stats(self, repo):
        result = get_weight_trend(repo)
        assert "records" in result
        assert "stats" in result
        assert isinstance(result["records"], list)

    def test_records_have_correct_shape(self, repo):
        result = get_weight_trend(repo)
        if result["records"]:
            rec = result["records"][0]
            assert "date" in rec
            assert "weight_kg" in rec
            assert isinstance(rec["date"], str)

    def test_json_safe(self, repo):
        result = get_weight_trend(repo)
        assert_json_safe(result)


# ---------------------------------------------------------------------------
# get_nutrition
# ---------------------------------------------------------------------------

class TestGetNutrition:
    def test_returns_totals_and_entries(self, repo):
        today = date.today()
        result = get_nutrition(repo, today)
        assert "totals" in result
        assert "entries" in result

    def test_totals_have_expected_keys(self, repo):
        today = date.today()
        result = get_nutrition(repo, today)
        totals = result["totals"]
        assert "calories" in totals
        assert "protein_g" in totals
        assert "carbs_g" in totals

    def test_totals_sum_correctly(self, repo):
        today = date.today()
        result = get_nutrition(repo, today)
        # Seeded: 350 + 90 = 440 calories
        assert result["totals"]["calories"] == pytest.approx(440.0)

    def test_entries_list_correct_count(self, repo):
        today = date.today()
        result = get_nutrition(repo, today)
        assert len(result["entries"]) == 2

    def test_defaults_to_today(self, repo):
        result = get_nutrition(repo)
        assert "totals" in result

    def test_json_safe(self, repo):
        result = get_nutrition(repo, date.today())
        assert_json_safe(result)

    def test_empty_day_returns_zeros(self, repo):
        future = date.today() + timedelta(days=365)
        result = get_nutrition(repo, future)
        assert result["totals"]["calories"] == 0
        assert result["entries"] == []


# ---------------------------------------------------------------------------
# get_nutrition_trend
# ---------------------------------------------------------------------------

class TestGetNutritionTrend:
    def test_returns_dict_with_expected_keys(self, repo):
        result = get_nutrition_trend(repo)
        assert "avg_calories" in result
        assert "days_with_data" in result

    def test_json_safe(self, repo):
        result = get_nutrition_trend(repo)
        assert_json_safe(result)


# ---------------------------------------------------------------------------
# get_training_load
# ---------------------------------------------------------------------------

class TestGetTrainingLoad:
    def test_returns_by_type_and_recent(self, repo):
        result = get_training_load(repo)
        assert "by_type" in result
        assert "recent" in result

    def test_by_type_has_seeded_activity(self, repo):
        result = get_training_load(repo)
        # seeded "running" activity today
        assert "running" in result["by_type"]

    def test_recent_has_seeded_training_entry(self, repo):
        result = get_training_load(repo)
        descriptions = [e["description"] for e in result["recent"]]
        assert any("Peito" in d for d in descriptions)

    def test_json_safe(self, repo):
        result = get_training_load(repo)
        assert_json_safe(result)


# ---------------------------------------------------------------------------
# get_activities
# ---------------------------------------------------------------------------

class TestGetActivities:
    def test_returns_list(self, repo):
        today = date.today()
        result = get_activities(repo, today - timedelta(days=7), today)
        assert isinstance(result, list)

    def test_contains_seeded_activities(self, repo):
        today = date.today()
        result = get_activities(repo, today - timedelta(days=7), today)
        names = [a["name"] for a in result]
        assert "Morning Run" in names
        assert "Cycling" in names

    def test_activity_has_expected_fields(self, repo):
        today = date.today()
        result = get_activities(repo, today, today)
        assert len(result) >= 1
        act = result[0]
        assert "garmin_activity_id" in act
        assert "name" in act
        assert "duration_min" in act
        assert "date" in act
        assert isinstance(act["date"], str)

    def test_empty_range_returns_empty(self, repo):
        future = date.today() + timedelta(days=365)
        result = get_activities(repo, future, future + timedelta(days=3))
        assert result == []

    def test_json_safe(self, repo):
        today = date.today()
        result = get_activities(repo, today - timedelta(days=7), today)
        assert_json_safe(result)


# ---------------------------------------------------------------------------
# get_goals
# ---------------------------------------------------------------------------

class TestGetGoals:
    def test_returns_dict(self, repo):
        result = get_goals(repo)
        assert isinstance(result, dict)

    def test_contains_seeded_goal(self, repo):
        result = get_goals(repo)
        assert "steps" in result
        assert result["steps"] == 9000.0

    def test_json_safe(self, repo):
        assert_json_safe(get_goals(repo))


# ---------------------------------------------------------------------------
# get_deficit
# ---------------------------------------------------------------------------

class TestGetDeficit:
    def test_returns_expected_shape(self, repo):
        today = date.today()
        result = get_deficit(repo, today)
        assert "date" in result
        assert "deficit_kcal" in result
        assert "deficit_pct" in result
        assert "burned" in result
        assert "eaten" in result

    def test_date_is_iso_string(self, repo):
        result = get_deficit(repo, date.today())
        assert isinstance(result["date"], str)
        assert result["date"] == date.today().isoformat()

    def test_has_burned_value_for_seeded_day(self, repo):
        result = get_deficit(repo, date.today())
        # We seeded total_calories=2400 (total != active+resting=2200, so total branch is used)
        assert result["burned"] is not None
        assert result["burned"] > 0

    def test_eaten_matches_food_entries(self, repo):
        result = get_deficit(repo, date.today())
        # Seeded: 350 + 90 = 440 calories
        assert result["eaten"] == pytest.approx(440.0)

    def test_deficit_computed(self, repo):
        result = get_deficit(repo, date.today())
        # Should have deficit since burned > eaten (2400 burned via total_calories, 440 eaten)
        assert result["deficit_kcal"] is not None
        assert result["deficit_kcal"] > 0

    def test_defaults_to_today(self, repo):
        result = get_deficit(repo)
        assert result["date"] == date.today().isoformat()

    def test_no_metrics_returns_nones(self, repo):
        future = date.today() + timedelta(days=365)
        result = get_deficit(repo, future)
        assert result["deficit_kcal"] is None
        assert result["deficit_pct"] is None
        assert result["burned"] is None

    def test_json_safe(self, repo):
        assert_json_safe(get_deficit(repo, date.today()))

    def test_food_logged_but_no_garmin_metrics(self, repo):
        """When food is logged but no Garmin metrics exist, burned/deficit must be None."""
        # today-100 is outside the 7-day metrics window and not used by other tests
        food_only_day = date.today() - timedelta(days=100)
        repo.save_food_entries(food_only_day, [
            {
                "name": "Test snack",
                "quantity": 1.0,
                "unit": "un",
                "calories": 200.0,
                "protein_g": 5.0,
                "fat_g": 8.0,
                "carbs_g": 20.0,
                "fiber_g": 1.0,
                "source": "manual",
            }
        ])
        result = get_deficit(repo, food_only_day)
        assert result["burned"] is None
        assert result["deficit_kcal"] is None
        assert result["deficit_pct"] is None
        assert result["eaten"] > 0

    def test_burned_and_deficit_are_consistent(self, repo):
        """burned and deficit_kcal must stay consistent if formatters.py logic changes.

        deficit_kcal = burned - eaten, so burned - result['eaten'] must equal deficit_kcal.
        This guards against the duplicate burn-logic in get_deficit drifting away from
        calculate_deficit's internal computation.

        Seed data: total_calories=2400, active=400, resting=1800 (active+resting=2200 != 2400).
        burned must use total (2400), not the active+resting fallback (2200).
        """
        result = get_deficit(repo, date.today())
        if result["deficit_kcal"] is not None and result["burned"] is not None:
            # Verify burned uses total_calories branch, not active+resting fallback
            assert result["burned"] == 2400, (
                f"burned should be total_calories (2400), not active+resting fallback "
                f"(2200). Got: {result['burned']}"
            )
            expected = result["burned"] - int(result["eaten"])
            assert result["deficit_kcal"] == expected, (
                f"burned ({result['burned']}) - eaten ({result['eaten']}) "
                f"should equal deficit_kcal ({result['deficit_kcal']})"
            )


# ---------------------------------------------------------------------------
# Server registration smoke test
# ---------------------------------------------------------------------------

class TestServerRegistration:
    def test_tool_count_is_11(self, repo):
        from src.mcp.server import build_server
        mcp = build_server(repo)
        tools = mcp._tool_manager.list_tools()
        assert len(tools) == 11

    def test_tool_names_present(self, repo):
        from src.mcp.server import build_server
        mcp = build_server(repo)
        names = {t.name for t in mcp._tool_manager.list_tools()}
        expected = {
            "get_daily_metrics",
            "get_metrics_range",
            "get_weekly_stats",
            "get_monthly_stats",
            "get_weight_trend",
            "get_nutrition",
            "get_nutrition_trend",
            "get_training_load",
            "get_activities",
            "get_goals",
            "get_deficit",
        }
        assert expected == names
