"""JSON-safe converter functions for MCP tool responses.

All date/datetime values are converted to ISO 8601 strings so that
json.dumps() works without a custom encoder.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from src.database.models import (
    DailyMetrics,
    FoodEntry,
    GarminActivity,
    TrainingEntry,
)


# ---------------------------------------------------------------------------
# DailyMetrics
# ---------------------------------------------------------------------------

def metrics_to_dict(row: DailyMetrics | None) -> dict | None:
    """Convert a DailyMetrics ORM row to a JSON-safe dict, or None."""
    if row is None:
        return None
    return {
        "date": row.date.isoformat() if row.date is not None else None,
        "sleep_hours": row.sleep_hours,
        "sleep_score": row.sleep_score,
        "sleep_quality": row.sleep_quality,
        "sleep_deep_min": row.sleep_deep_min,
        "sleep_light_min": row.sleep_light_min,
        "sleep_rem_min": row.sleep_rem_min,
        "sleep_awake_min": row.sleep_awake_min,
        "steps": row.steps,
        "active_calories": row.active_calories,
        "resting_calories": row.resting_calories,
        "total_calories": row.total_calories,
        "floors_ascended": row.floors_ascended,
        "intensity_moderate_min": row.intensity_moderate_min,
        "intensity_vigorous_min": row.intensity_vigorous_min,
        "resting_heart_rate": row.resting_heart_rate,
        "avg_stress": row.avg_stress,
        "body_battery_high": row.body_battery_high,
        "body_battery_low": row.body_battery_low,
        "spo2_avg": row.spo2_avg,
        "weight_kg": row.weight_kg,
        "synced_at": row.synced_at.isoformat() if row.synced_at is not None else None,
        "garmin_sync_success": row.garmin_sync_success,
    }


def metrics_list_to_dicts(rows: list[DailyMetrics]) -> list[dict]:
    """Convert a list of DailyMetrics ORM rows to JSON-safe dicts."""
    return [metrics_to_dict(row) for row in rows]  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Generic stats dict (weekly/monthly stats, weight stats)
# ---------------------------------------------------------------------------

def stats_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict with all date/datetime values converted to ISO strings.

    Numeric, bool, None, and string values pass through unchanged.
    Does NOT mutate the input dict.
    """
    result: dict[str, Any] = {}
    for key, value in d.items():
        if isinstance(value, datetime):
            # datetime subclasses date — check datetime first
            result[key] = value.isoformat()
        elif isinstance(value, date):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Weight records
# ---------------------------------------------------------------------------

def weight_records_to_list(pairs: list[tuple[date, float]]) -> list[dict]:
    """Convert (date, kg) tuples to a list of JSON-safe dicts."""
    return [{"date": d.isoformat(), "weight_kg": kg} for d, kg in pairs]


# ---------------------------------------------------------------------------
# GarminActivity
# ---------------------------------------------------------------------------

def activity_to_dict(row: GarminActivity) -> dict:
    """Convert a GarminActivity ORM row to a JSON-safe dict."""
    return {
        "garmin_activity_id": row.garmin_activity_id,
        "activity_id": row.garmin_activity_id,
        "date": row.date.isoformat() if row.date is not None else None,
        "name": row.name,
        "type_key": row.type_key,
        "duration_min": row.duration_min,
        "calories": row.calories,
        "distance_km": row.distance_km,
        "avg_hr": getattr(row, "avg_hr", None),
        "max_hr": getattr(row, "max_hr", None),
        "is_indoor": getattr(row, "is_indoor", None),
        "total_sets": getattr(row, "total_sets", None),
        "total_reps": getattr(row, "total_reps", None),
        "min_weight_kg": getattr(row, "min_weight_kg", None),
        "max_weight_kg": getattr(row, "max_weight_kg", None),
        "synced_at": row.synced_at.isoformat() if row.synced_at is not None else None,
    }


def activity_list_to_dicts(rows: list[GarminActivity]) -> list[dict]:
    """Convert a list of GarminActivity ORM rows to JSON-safe dicts."""
    return [activity_to_dict(row) for row in rows]


# ---------------------------------------------------------------------------
# TrainingEntry
# ---------------------------------------------------------------------------

def training_entry_to_dict(row: TrainingEntry) -> dict:
    """Convert a TrainingEntry ORM row to a JSON-safe dict."""
    return {
        "id": row.id,
        "date": row.date.isoformat() if row.date is not None else None,
        "description": row.description,
        "created_at": row.created_at.isoformat() if row.created_at is not None else None,
    }


def training_entry_list_to_dicts(rows: list[TrainingEntry]) -> list[dict]:
    """Convert a list of TrainingEntry ORM rows to JSON-safe dicts."""
    return [training_entry_to_dict(row) for row in rows]


# ---------------------------------------------------------------------------
# FoodEntry
# ---------------------------------------------------------------------------

def food_entry_to_dict(row: FoodEntry) -> dict:
    """Convert a FoodEntry ORM row to a JSON-safe dict."""
    return {
        "id": row.id,
        "date": row.date.isoformat() if row.date is not None else None,
        "name": row.name,
        "quantity": row.quantity,
        "unit": row.unit,
        "calories": row.calories,
        "protein_g": row.protein_g,
        "fat_g": row.fat_g,
        "carbs_g": row.carbs_g,
        "fiber_g": row.fiber_g,
        "source": row.source,
        "barcode": row.barcode,
        "created_at": row.created_at.isoformat() if row.created_at is not None else None,
    }


def food_entry_list_to_dicts(rows: list[FoodEntry]) -> list[dict]:
    """Convert a list of FoodEntry ORM rows to JSON-safe dicts."""
    return [food_entry_to_dict(row) for row in rows]
