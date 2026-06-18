"""Tests for src/nutrition/fatsecret_mapper.py."""

from __future__ import annotations

import pytest

from src.nutrition.fatsecret_mapper import map_fatsecret_entries, map_fatsecret_entry, normalize_food_entries

# ---------------------------------------------------------------------------
# Shared fixture: verbatim API response envelope (single-entry dict case)
# ---------------------------------------------------------------------------

SINGLE_ENTRY_DICT = {
    "calories": "121",
    "carbohydrate": "1.89",
    "date_int": "20622",
    "fat": "2.43",
    "fiber": "0.4",
    "food_entry_description": "0.3 serving Prozis 100% Vegan Protein Baunilha",
    "food_entry_id": "23984959963",
    "food_entry_name": "Prozis 100% Vegan Protein Baunilha",
    "food_id": "46844289",
    "meal": "Breakfast",
    "number_of_units": "0.300",
    "protein": "22.50",
    "saturated_fat": "0.570",
    "serving_id": "40105019",
    "sodium": "596",
    "sugar": "0.09",
}

MULTI_ENTRY_LIST = [
    SINGLE_ENTRY_DICT,
    {
        "calories": "200",
        "carbohydrate": "40.00",
        "date_int": "20622",
        "fat": "1.00",
        "food_entry_id": "99999",
        "food_entry_name": "Oats",
        "food_id": "111",
        "meal": "Breakfast",
        "number_of_units": "1.000",
        "protein": "5.00",
        "serving_id": "222",
    },
]


# ---------------------------------------------------------------------------
# normalize_food_entries tests
# ---------------------------------------------------------------------------


class TestNormalizeFoodEntries:
    def test_single_dict_becomes_list(self):
        result = normalize_food_entries({"food_entry": SINGLE_ENTRY_DICT})
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0] is SINGLE_ENTRY_DICT

    def test_list_passthrough(self):
        result = normalize_food_entries({"food_entry": MULTI_ENTRY_LIST})
        assert isinstance(result, list)
        assert len(result) == 2

    def test_null_food_entries_returns_empty(self):
        result = normalize_food_entries(None)
        assert result == []

    def test_empty_food_entries_returns_empty(self):
        result = normalize_food_entries({})
        assert result == []

    def test_food_entry_absent_returns_empty(self):
        # food_entries present but no food_entry key
        result = normalize_food_entries({"other_key": "value"})
        assert result == []


# ---------------------------------------------------------------------------
# map_fatsecret_entry tests (single raw dict → FoodEntry-compatible dict)
# ---------------------------------------------------------------------------


class TestMapFatSecretEntry:
    def setup_method(self):
        self.mapped = map_fatsecret_entry(SINGLE_ENTRY_DICT)

    def test_name(self):
        assert self.mapped["name"] == "Prozis 100% Vegan Protein Baunilha"

    def test_calories(self):
        assert self.mapped["calories"] == 121.0

    def test_protein_g(self):
        assert self.mapped["protein_g"] == 22.5

    def test_fat_g(self):
        assert self.mapped["fat_g"] == 2.43

    def test_carbs_g(self):
        assert self.mapped["carbs_g"] == 1.89

    def test_fiber_g(self):
        assert self.mapped["fiber_g"] == 0.4

    def test_quantity(self):
        assert self.mapped["quantity"] == 0.3

    def test_unit(self):
        assert self.mapped["unit"] == "serving"

    def test_source(self):
        assert self.mapped["source"] == "fatsecret"

    def test_barcode_stores_food_entry_id(self):
        # barcode column reused as dedup key for FatSecret entries
        assert self.mapped["barcode"] == "23984959963"

    def test_no_date_key(self):
        # date must not appear — the repo's save_food_entries receives day separately
        assert "date" not in self.mapped

    def test_fiber_missing_returns_none(self):
        raw = dict(SINGLE_ENTRY_DICT)
        del raw["fiber"]
        mapped = map_fatsecret_entry(raw)
        assert mapped["fiber_g"] is None

    def test_fiber_empty_string_returns_none(self):
        raw = dict(SINGLE_ENTRY_DICT)
        raw["fiber"] = ""
        mapped = map_fatsecret_entry(raw)
        assert mapped["fiber_g"] is None

    def test_fiber_none_returns_none(self):
        raw = dict(SINGLE_ENTRY_DICT)
        raw["fiber"] = None
        mapped = map_fatsecret_entry(raw)
        assert mapped["fiber_g"] is None

    def test_calories_none_returns_none(self):
        raw = dict(SINGLE_ENTRY_DICT)
        raw["calories"] = None
        mapped = map_fatsecret_entry(raw)
        assert mapped["calories"] is None

    def test_calories_empty_string_returns_none(self):
        raw = dict(SINGLE_ENTRY_DICT)
        raw["calories"] = ""
        mapped = map_fatsecret_entry(raw)
        assert mapped["calories"] is None


# ---------------------------------------------------------------------------
# map_fatsecret_entries tests (list of raw dicts)
# ---------------------------------------------------------------------------


class TestMapFatSecretEntries:
    def test_maps_all_entries(self):
        result = map_fatsecret_entries(MULTI_ENTRY_LIST)
        assert len(result) == 2

    def test_empty_list_returns_empty(self):
        assert map_fatsecret_entries([]) == []

    def test_second_entry_fiber_absent_is_none(self):
        result = map_fatsecret_entries(MULTI_ENTRY_LIST)
        # second entry has no fiber key
        assert result[1]["fiber_g"] is None

    def test_second_entry_name(self):
        result = map_fatsecret_entries(MULTI_ENTRY_LIST)
        assert result[1]["name"] == "Oats"
