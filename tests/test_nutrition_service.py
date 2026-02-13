"""Tests for src/nutrition/service.py — NutritionService orchestration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.nutrition.openfoodfacts import NutritionData
from src.nutrition.parser import ParsedFoodItem
from src.nutrition.service import FoodItemResult, NutritionService


def _make_nutrition(kcal=200.0, protein=10.0, fat=5.0, carbs=30.0, fiber=2.0, serving=None):
    return NutritionData(
        product_name="Test Product",
        calories_per_100g=kcal,
        protein_per_100g=protein,
        fat_per_100g=fat,
        carbs_per_100g=carbs,
        fiber_per_100g=fiber,
        serving_size_g=serving,
    )


class TestCalculateNutrients:
    def setup_method(self):
        self.svc = NutritionService.__new__(NutritionService)
        self.svc._api_key = "fake"

    def test_grams_scaling(self):
        n = _make_nutrition(kcal=200.0)
        result = self.svc._calculate_nutrients(n, 150.0, "g")
        assert result["calories"] == 300.0  # 200 * 1.5

    def test_unit_with_serving(self):
        n = _make_nutrition(kcal=200.0, serving=50.0)
        result = self.svc._calculate_nutrients(n, 2.0, "un")
        # factor = (50g * 2) / 100 = 1.0 → 200 * 1.0 = 200
        assert result["calories"] == 200.0

    def test_unit_no_serving_assumes_100g(self):
        n = _make_nutrition(kcal=200.0, serving=None)
        result = self.svc._calculate_nutrients(n, 1.0, "un")
        # factor = (100g * 1) / 100 = 1.0 → 200 kcal
        assert result["calories"] == 200.0

    def test_ml_treated_as_grams(self):
        n = _make_nutrition(kcal=100.0)
        result = self.svc._calculate_nutrients(n, 200.0, "ml")
        assert result["calories"] == 200.0


@patch("src.nutrition.service.search_product")
@patch("src.nutrition.service.parse_food_text")
def test_process_text_off_found(mock_parse, mock_search):
    """Text with 2 items both found in OpenFoodFacts."""
    mock_parse.return_value = [
        ParsedFoodItem(name="arroz cozido", quantity=150, unit="g"),
        ParsedFoodItem(name="frango grelhado", quantity=200, unit="g"),
    ]
    mock_search.return_value = _make_nutrition(kcal=130.0)

    svc = NutritionService("fake-key")
    results = svc.process_text("150g de arroz e 200g de frango")

    assert len(results) == 2
    assert all(r.source == "openfoodfacts" for r in results)
    assert results[0].calories == pytest.approx(195.0)  # 130 * 1.5


@patch("src.nutrition.service.NutritionService._estimate_nutrition")
@patch("src.nutrition.service.search_product")
@patch("src.nutrition.service.parse_food_text")
def test_process_text_fallback_to_claude(mock_parse, mock_search, mock_estimate):
    """Item not found in OFF falls back to Claude estimate."""
    mock_parse.return_value = [ParsedFoodItem(name="produto xpto", quantity=1, unit="un")]
    mock_search.return_value = None
    mock_estimate.return_value = {
        "calories_per_100g": 250.0, "protein_per_100g": 8.0,
        "fat_per_100g": 10.0, "carbs_per_100g": 35.0, "fiber_per_100g": 1.0,
    }

    svc = NutritionService("fake-key")
    results = svc.process_text("1 produto xpto")

    assert len(results) == 1
    assert results[0].source == "claude_estimate"


@patch("src.nutrition.service.lookup_barcode")
@patch("src.nutrition.service.decode_barcode")
def test_process_barcode_found(mock_decode, mock_lookup):
    """Valid barcode returns FoodItemResult with source='barcode'."""
    mock_decode.return_value = "3017620422003"
    mock_lookup.return_value = _make_nutrition(kcal=200.0, serving=30.0)

    svc = NutritionService("fake-key")
    result = svc.process_barcode(b"image")

    assert result is not None
    assert result.source == "barcode"
    assert result.barcode == "3017620422003"


@patch("src.nutrition.service.decode_barcode")
def test_process_barcode_decode_fails(mock_decode):
    """Barcode not detected returns None."""
    mock_decode.return_value = None

    svc = NutritionService("fake-key")
    result = svc.process_barcode(b"image")
    assert result is None


@patch("src.nutrition.service.lookup_barcode")
@patch("src.nutrition.service.decode_barcode")
def test_process_barcode_not_in_off(mock_decode, mock_lookup):
    """Barcode decoded but product not in OFF returns None."""
    mock_decode.return_value = "9999999999999"
    mock_lookup.return_value = None

    svc = NutritionService("fake-key")
    result = svc.process_barcode(b"image")
    assert result is None
