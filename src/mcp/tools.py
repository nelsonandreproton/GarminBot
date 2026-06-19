"""Pure tool functions for the GarminBot MCP server.

Each function takes a Repository as first argument so they can be unit-tested
without the MCP layer. They return JSON-safe dicts/lists via formatting.py.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.database.repository import Repository
from src.mcp.formatting import (
    activity_list_to_dicts,
    food_entry_list_to_dicts,
    metrics_list_to_dicts,
    metrics_to_dict,
    stats_dict,
    training_entry_list_to_dicts,
    weight_records_to_list,
)
from src.telegram.formatters import calculate_deficit


def get_daily_metrics(repo: Repository, day: date | None = None) -> dict | None:
    """Return metrics for a single day (today by default), or None if no data."""
    target = day or date.today()
    return metrics_to_dict(repo.get_metrics_by_date(target))


def get_metrics_range(repo: Repository, start: date, end: date) -> list[dict]:
    """Return metrics rows for all days in [start, end], ordered by date."""
    return metrics_list_to_dicts(repo.get_metrics_range(start, end))


def get_weekly_stats(repo: Repository, end_date: date | None = None) -> dict:
    """Return 7-day aggregate stats ending on end_date (today by default)."""
    target = end_date or date.today()
    return stats_dict(repo.get_weekly_stats(target))


def get_monthly_stats(repo: Repository, end_date: date | None = None) -> dict:
    """Return 30-day aggregate stats ending on end_date (today by default)."""
    target = end_date or date.today()
    return stats_dict(repo.get_monthly_stats(target))


def get_weight_trend(repo: Repository, days: int = 90) -> dict:
    """Return weight trend: list of records (oldest first) plus current-week stats."""
    records = weight_records_to_list(repo.get_weight_records_range(days))
    weekly = stats_dict(repo.get_weekly_weight_stats(date.today()))
    return {"records": records, "stats": weekly}


def get_nutrition(repo: Repository, day: date | None = None) -> dict:
    """Return daily nutrition totals plus individual food entries for a day."""
    target = day or date.today()
    totals = repo.get_daily_nutrition(target)
    entries = food_entry_list_to_dicts(repo.get_food_entries(target))
    return {"totals": totals, "entries": entries}


def get_nutrition_trend(repo: Repository, end_date: date | None = None) -> dict:
    """Return 7-day average nutrition figures ending on end_date (today by default)."""
    target = end_date or date.today()
    return repo.get_weekly_nutrition(target)


def get_training_load(repo: Repository, end_date: date | None = None) -> dict:
    """Return weekly training load by type plus recent training log entries."""
    target = end_date or date.today()
    by_type = repo.get_weekly_training_load(target)
    recent = training_entry_list_to_dicts(repo.get_recent_training(7))
    return {"by_type": by_type, "recent": recent}


def get_activities(repo: Repository, start: date, end: date) -> list[dict]:
    """Return Garmin activities in [start, end], ordered by date then activity_id."""
    return activity_list_to_dicts(repo.get_garmin_activities_range(start, end))


def get_goals(repo: Repository) -> dict:
    """Return all user goals as {metric: target_value}."""
    return repo.get_goals()


def get_deficit(repo: Repository, day: date | None = None) -> dict[str, Any]:
    """Return caloric deficit calculation for a day.

    Combines Garmin burn data with food diary intake.
    Positive deficit_kcal = ate less than burned. Negative = surplus.
    Returns None values for deficit/pct/burned when data is insufficient.
    """
    target = day or date.today()
    m = repo.get_metrics_by_date(target)
    n = repo.get_daily_nutrition(target)
    eaten = n["calories"]  # always present, 0 if no entries

    if m is None:
        return {
            "date": target.isoformat(),
            "deficit_kcal": None,
            "deficit_pct": None,
            "burned": None,
            "eaten": eaten,
        }

    # Mirror calculate_deficit's logic for the "burned" value
    active = m.active_calories
    resting = m.resting_calories
    total = m.total_calories
    if total is not None and total > 0:
        burned = total
    else:
        burned = (active or 0) + (resting or 0)
    if burned == 0:
        burned = None

    deficit_kcal, deficit_pct = calculate_deficit(
        active, resting, eaten if eaten > 0 else None, total
    )

    return {
        "date": target.isoformat(),
        "deficit_kcal": deficit_kcal,
        "deficit_pct": deficit_pct,
        "burned": burned,
        "eaten": eaten,
    }
