"""FatSecret API response mapper: raw entry dicts → FoodEntry-compatible dicts.

Design choice — dedup key via barcode column:
    FoodEntry.barcode is a nullable String(50) column currently unused by FatSecret.
    We store the FatSecret food_entry_id there. This survives the repository's
    `{k: v for k, v in entry.items() if hasattr(FoodEntry, k)}` filter, making
    dedup possible at the repo layer without schema changes. The alternative —
    returning a `food_entry_id` key — would be silently dropped by that filter.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _parse_float(value: object) -> float | None:
    """Convert a string/number value to float, returning None on failure or empty."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.debug("Could not parse %r as float", value)
        return None


def normalize_food_entries(food_entries_obj: object) -> list[dict]:
    """Normalise the FatSecret food_entries value to a flat list of raw entry dicts.

    The API returns three shapes:
      - None / null → empty day → []
      - {"food_entry": {...}} → single entry → [that dict]
      - {"food_entry": [{...}, {...}]} → multiple entries → [list as-is]
    """
    if not food_entries_obj or not isinstance(food_entries_obj, dict):
        return []

    food_entry = food_entries_obj.get("food_entry")
    if food_entry is None:
        return []
    if isinstance(food_entry, dict):
        return [food_entry]
    if isinstance(food_entry, list):
        return food_entry
    return []


def map_fatsecret_entry(raw: dict) -> dict:
    """Convert one raw FatSecret food_entry dict into a FoodEntry-compatible dict.

    All string-encoded numeric fields are parsed to float.
    Missing or empty numeric fields become None (graceful degradation).
    The `barcode` column is reused to store food_entry_id as a stable dedup key.
    The `date` key is intentionally omitted — the caller (save_food_entries) sets it.
    """
    return {
        "name": raw.get("food_entry_name", ""),
        "calories": _parse_float(raw.get("calories")),
        "protein_g": _parse_float(raw.get("protein")),
        "fat_g": _parse_float(raw.get("fat")),
        "carbs_g": _parse_float(raw.get("carbohydrate")),
        "fiber_g": _parse_float(raw.get("fiber")),
        "quantity": _parse_float(raw.get("number_of_units")) or 1.0,
        "unit": "serving",
        "source": "fatsecret",
        # Reuse barcode column as the FatSecret dedup key (food_entry_id is unique per diary entry)
        "barcode": raw.get("food_entry_id"),
    }


def map_fatsecret_entries(raw_list: list[dict]) -> list[dict]:
    """Map a list of raw FatSecret entry dicts to FoodEntry-compatible dicts."""
    return [map_fatsecret_entry(entry) for entry in raw_list]
