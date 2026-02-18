"""Tests for src/telegram/formatters.py."""

from datetime import date

import pytest

from src.telegram.formatters import (
    calculate_deficit,
    format_daily_summary,
    format_error_message,
    format_food_confirmation,
    format_goals,
    format_monthly_report,
    format_nutrition_day,
    format_nutrition_summary,
    format_remaining_macros,
    format_weekly_report,
)


def test_format_daily_summary_basic():
    metrics = {
        "date": date(2026, 2, 13),
        "sleep_hours": 7.5,
        "sleep_score": 82,
        "sleep_quality": "Excelente",
        "steps": 12340,
        "active_calories": 487,
        "resting_calories": 1680,
    }
    text = format_daily_summary(metrics)
    assert "13/02/2026" in text
    assert "7h 30min" in text
    assert "82/100" in text
    assert "12.340" in text
    assert "Excelente" in text


def test_format_daily_summary_with_weekly_comparison():
    metrics = {
        "date": date(2026, 2, 13),
        "sleep_hours": 7.5,
        "sleep_score": 82,
        "sleep_quality": "Bom",
        "steps": 12000,
        "active_calories": 450,
        "resting_calories": 1700,
    }
    weekly = {
        "sleep_avg_hours": 7.0,
        "steps_avg": 10000,
    }
    text = format_daily_summary(metrics, weekly_stats=weekly)
    assert "Comparação semanal" in text
    assert "+30min" in text  # 7.5 - 7.0 = 30min


def test_format_daily_summary_none_values():
    metrics = {
        "date": date(2026, 2, 13),
        "sleep_hours": None,
        "sleep_score": None,
        "sleep_quality": None,
        "steps": None,
        "active_calories": None,
        "resting_calories": None,
    }
    text = format_daily_summary(metrics)
    assert "—" in text


def test_format_weekly_report():
    stats = {
        "start_date": date(2026, 2, 7),
        "end_date": date(2026, 2, 13),
        "sleep_avg_hours": 7.25,
        "sleep_avg_score": 79,
        "sleep_best_hours": 8.0,
        "sleep_best_day": date(2026, 2, 8),
        "sleep_worst_hours": 6.5,
        "sleep_worst_day": date(2026, 2, 11),
        "steps_total": 78920,
        "steps_avg": 11274,
        "active_calories_total": 3214,
        "resting_calories_total": 11760,
    }
    text = format_weekly_report(stats)
    assert "Relatório Semanal" in text
    assert "78.920" in text
    assert "79/100" in text
    assert "Domingo" in text  # Feb 8 is Sunday


def test_format_error_message():
    text = format_error_message("sync Garmin", ValueError("bad credentials"))
    assert "Erro" in text
    assert "bad credentials" in text


def test_format_monthly_report():
    stats = {
        "start_date": date(2026, 1, 14),
        "end_date": date(2026, 2, 13),
        "days_with_data": 28,
        "sleep_avg_hours": 7.1,
        "steps_total": 320000,
        "steps_avg": 11428,
        "active_calories_total": 14000,
    }
    text = format_monthly_report(stats)
    assert "Mensal" in text
    assert "320.000" in text


# ------------------------------------------------------------------ #
# Nutrition formatter tests                                            #
# ------------------------------------------------------------------ #

def test_calculate_deficit_positive():
    """Burned more than eaten → positive deficit."""
    deficit, pct = calculate_deficit(active_cal=500, resting_cal=1600, eaten_cal=1850.0)
    assert deficit == 250
    assert pct == pytest.approx(11.9, abs=0.2)


def test_calculate_deficit_surplus():
    """Ate more than burned → negative deficit (surplus)."""
    deficit, pct = calculate_deficit(active_cal=300, resting_cal=1500, eaten_cal=2500.0)
    assert deficit == -700
    assert pct < 0


def test_calculate_deficit_no_garmin_data():
    deficit, pct = calculate_deficit(active_cal=None, resting_cal=None, eaten_cal=2000.0)
    assert deficit is None
    assert pct is None


def test_calculate_deficit_no_food_data():
    deficit, pct = calculate_deficit(active_cal=500, resting_cal=1500, eaten_cal=None)
    assert deficit is None
    assert pct is None


def test_calculate_deficit_prefers_total_calories():
    """When total_cal is provided, it should be used instead of active+resting."""
    deficit, pct = calculate_deficit(
        active_cal=500, resting_cal=1600, eaten_cal=1500.0, total_cal=1668,
    )
    # total_cal=1668 used instead of 500+1600=2100
    assert deficit == 168
    assert pct == pytest.approx(10.1, abs=0.2)


def test_calculate_deficit_falls_back_without_total():
    """Without total_cal, falls back to active+resting."""
    deficit, pct = calculate_deficit(
        active_cal=500, resting_cal=1600, eaten_cal=1850.0, total_cal=None,
    )
    assert deficit == 250  # (500+1600) - 1850


def test_format_nutrition_summary_with_deficit():
    nutrition = {
        "calories": 1850.0, "protein_g": 120.0, "fat_g": 65.0,
        "carbs_g": 210.0, "fiber_g": 25.0,
        "active_calories": 500, "resting_calories": 1600,
    }
    text = format_nutrition_summary(nutrition)
    assert "Nutrição" in text
    assert "1850 kcal" in text
    assert "Défice" in text


def test_format_food_confirmation():
    from src.nutrition.service import FoodItemResult
    items = [
        FoodItemResult(name="ovo", quantity=2, unit="un",
                       calories=140.0, protein_g=12.0, fat_g=10.0, carbs_g=1.0, fiber_g=0.0,
                       source="openfoodfacts"),
        FoodItemResult(name="arroz cozido", quantity=150, unit="g",
                       calories=195.0, protein_g=4.0, fat_g=0.5, carbs_g=42.0, fiber_g=1.0,
                       source="openfoodfacts"),
    ]
    text = format_food_confirmation(items)
    assert "Registar refeição" in text
    assert "Ovo" in text
    assert "335 kcal" in text  # 140 + 195


def test_format_daily_summary_with_nutrition():
    metrics = {
        "date": date(2026, 2, 13),
        "sleep_hours": 7.5, "sleep_score": 80, "sleep_quality": "Bom",
        "steps": 10000, "active_calories": 500, "resting_calories": 1600,
        "nutrition": {
            "calories": 1800.0, "protein_g": 110.0, "fat_g": 60.0,
            "carbs_g": 220.0, "fiber_g": 22.0, "entry_count": 3,
            "active_calories": 500, "resting_calories": 1600,
        },
    }
    text = format_daily_summary(metrics)
    assert "Nutrição" in text
    assert "1800 kcal" in text


# ------------------------------------------------------------------ #
# Macro goals & remaining macros tests                                 #
# ------------------------------------------------------------------ #

def test_format_goals_with_macros():
    goals = {
        "steps": 10000.0, "sleep_hours": 7.0,
        "calories": 1750.0, "protein_g": 150.0, "fat_g": 60.0, "carbs_g": 200.0,
    }
    text = format_goals(goals)
    assert "Nutrição" in text
    assert "1750 kcal" in text
    assert "150g" in text
    assert "60g" in text
    assert "200g" in text


def test_format_goals_without_macros():
    goals = {"steps": 10000.0, "sleep_hours": 7.0}
    text = format_goals(goals)
    assert "Nutrição" not in text
    assert "10.000" in text


def test_format_remaining_macros_all_goals():
    totals = {"calories": 800.0, "protein_g": 50.0, "fat_g": 25.0, "carbs_g": 80.0}
    goals = {"calories": 1750.0, "protein_g": 150.0, "fat_g": 60.0, "carbs_g": 200.0}
    result = format_remaining_macros(totals, goals)
    assert result is not None
    assert "950 kcal" in result
    assert "P: 100g" in result
    assert "G: 35g" in result
    assert "HC: 120g" in result


def test_format_remaining_macros_no_goals_returns_none():
    totals = {"calories": 800.0, "protein_g": 50.0}
    goals = {"steps": 10000.0, "sleep_hours": 7.0}
    result = format_remaining_macros(totals, goals)
    assert result is None


def test_format_remaining_macros_partial_goals():
    totals = {"calories": 1000.0, "protein_g": 60.0, "fat_g": 30.0, "carbs_g": 100.0}
    goals = {"calories": 1750.0, "protein_g": 150.0}
    result = format_remaining_macros(totals, goals)
    assert "750 kcal" in result
    assert "P: 90g" in result
    assert "G:" not in result  # no fat goal set


def test_format_remaining_macros_with_garmin_deficit():
    totals = {"calories": 1200.0, "protein_g": 80.0}
    goals = {"calories": 1750.0}
    garmin = {"total_calories": 2100, "active_calories": 500, "resting_calories": 1600}
    result = format_remaining_macros(totals, goals, garmin)
    assert "Défice vs Garmin" in result
    assert "-900" in result  # 1200 - 2100 = -900


def test_format_remaining_macros_with_garmin_surplus():
    totals = {"calories": 2500.0, "protein_g": 80.0}
    goals = {"calories": 1750.0}
    garmin = {"total_calories": 2100, "active_calories": 500, "resting_calories": 1600}
    result = format_remaining_macros(totals, goals, garmin)
    assert "Excedente vs Garmin" in result
    assert "+400" in result  # 2500 - 2100 = +400


def test_format_remaining_macros_garmin_fallback_no_total():
    """Without total_calories, falls back to active+resting."""
    totals = {"calories": 1200.0, "protein_g": 80.0}
    goals = {"calories": 1750.0}
    garmin = {"active_calories": 500, "resting_calories": 1600}
    result = format_remaining_macros(totals, goals, garmin)
    assert "Défice vs Garmin" in result
    assert "-900" in result  # 1200 - 2100 = -900


# ------------------------------------------------------------------ #
# show_sleep parameter tests                                           #
# ------------------------------------------------------------------ #

def test_format_daily_summary_no_sleep_section():
    """show_sleep=False omits the Sono section (used by /hoje)."""
    metrics = {
        "date": date(2026, 2, 13),
        "sleep_hours": 7.5, "sleep_score": 80, "sleep_quality": "Bom",
        "steps": 8000, "active_calories": 400, "resting_calories": 1600,
    }
    text = format_daily_summary(metrics, show_sleep=False)
    assert "Sono" not in text
    assert "Atividade" in text
    assert "8.000" in text


def test_format_daily_summary_weekly_comparison_no_sleep_when_today():
    """show_sleep=False also omits 'Sono médio' from weekly comparison."""
    metrics = {
        "date": date(2026, 2, 13),
        "sleep_hours": 7.5, "sleep_score": 80, "sleep_quality": "Bom",
        "steps": 8000, "active_calories": 400, "resting_calories": 1600,
    }
    weekly = {"sleep_avg_hours": 7.0, "steps_avg": 9000}
    text = format_daily_summary(metrics, weekly_stats=weekly, show_sleep=False)
    assert "Sono médio" not in text
    assert "Passos médios" in text
    assert "Comparação semanal" in text
