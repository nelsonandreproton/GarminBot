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
    assert results[0].source == "llm_estimate"


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


# ------------------------------------------------------------------ #
# lookup_ean                                                           #
# ------------------------------------------------------------------ #

@patch("src.nutrition.service.lookup_barcode")
def test_lookup_ean_found(mock_lookup):
    """Valid EAN returns FoodItemResult with source='barcode'."""
    mock_lookup.return_value = _make_nutrition(kcal=200.0, serving=30.0)
    mock_lookup.return_value = NutritionData(
        product_name="Pudim Proteína",
        calories_per_100g=148.0,
        protein_per_100g=19.0,
        fat_per_100g=3.0,
        carbs_per_100g=10.0,
        fiber_per_100g=1.0,
        serving_size_g=125.0,
    )

    svc = NutritionService("fake-key")
    result = svc.lookup_ean("5601312308027")

    assert result is not None
    assert result.source == "barcode"
    assert result.barcode == "5601312308027"
    assert result.name == "Pudim Proteína"
    assert result.quantity == 1.0
    assert result.unit == "un"
    # calories: 148 * (125/100) = 185.0
    assert result.calories == pytest.approx(185.0)
    mock_lookup.assert_called_once_with("5601312308027")


@patch("src.nutrition.service.lookup_barcode")
def test_lookup_ean_not_found(mock_lookup):
    """EAN not in OpenFoodFacts returns None."""
    mock_lookup.return_value = None

    svc = NutritionService("fake-key")
    result = svc.lookup_ean("9999999999999")

    assert result is None
    mock_lookup.assert_called_once_with("9999999999999")


@patch("src.nutrition.service.lookup_barcode")
def test_lookup_ean_does_not_call_decode_barcode(mock_lookup):
    """lookup_ean must NOT try to decode any image — no decode_barcode call."""
    mock_lookup.return_value = None

    with patch("src.nutrition.service.decode_barcode") as mock_decode:
        svc = NutritionService("fake-key")
        svc.lookup_ean("1234567890123")
        mock_decode.assert_not_called()


# ------------------------------------------------------------------ #
# Fallback chain: OFF → USDA → API-Ninjas → LLM                      #
# ------------------------------------------------------------------ #

@patch("src.nutrition.usda.search_product")
@patch("src.nutrition.service.search_product")
@patch("src.nutrition.service.parse_food_text")
def test_process_text_usda_fallback(mock_parse, mock_off, mock_usda):
    """OFF miss + USDA key configured → USDA hit → source='usda'."""
    mock_parse.return_value = [ParsedFoodItem(name="brown rice", quantity=150, unit="g")]
    mock_off.return_value = None
    mock_usda.return_value = _make_nutrition(kcal=111.0, protein=2.6, fat=0.9, carbs=23.0)

    svc = NutritionService("fake-key", usda_api_key="usda-key")
    results = svc.process_text("150g brown rice")

    assert len(results) == 1
    assert results[0].source == "usda"
    assert results[0].calories == pytest.approx(166.5)  # 111 * 1.5
    mock_usda.assert_called_once_with("brown rice", "usda-key")


@patch("src.nutrition.api_ninjas.search_product")
@patch("src.nutrition.usda.search_product")
@patch("src.nutrition.service.search_product")
@patch("src.nutrition.service.parse_food_text")
def test_process_text_api_ninjas_fallback(mock_parse, mock_off, mock_usda, mock_ninjas):
    """OFF miss + USDA miss → API-Ninjas hit → source='api_ninjas'."""
    mock_parse.return_value = [ParsedFoodItem(name="banana", quantity=100, unit="g")]
    mock_off.return_value = None
    mock_usda.return_value = None
    mock_ninjas.return_value = _make_nutrition(kcal=89.0, protein=1.1, fat=0.3, carbs=23.0)

    svc = NutritionService("fake-key", usda_api_key="usda-key", api_ninjas_key="ninjas-key")
    results = svc.process_text("100g banana")

    assert len(results) == 1
    assert results[0].source == "api_ninjas"
    assert results[0].calories == pytest.approx(89.0)  # 100g * 89/100
    mock_ninjas.assert_called_once_with("banana", "ninjas-key")


@patch("src.nutrition.service.NutritionService._estimate_nutrition")
@patch("src.nutrition.service.search_product")
@patch("src.nutrition.service.parse_food_text")
def test_llm_only_used_as_last_resort(mock_parse, mock_off, mock_llm):
    """With no API keys, LLM is called immediately after OFF miss."""
    mock_parse.return_value = [ParsedFoodItem(name="unusual food", quantity=1, unit="un")]
    mock_off.return_value = None
    mock_llm.return_value = {
        "calories_per_100g": 200.0, "protein_per_100g": 5.0,
        "fat_per_100g": 8.0, "carbs_per_100g": 30.0, "fiber_per_100g": 1.0,
    }

    svc = NutritionService("fake-key")  # no USDA or API-Ninjas keys
    results = svc.process_text("1 unusual food")

    assert len(results) == 1
    assert results[0].source == "llm_estimate"
    mock_llm.assert_called_once()


@patch("src.nutrition.service.NutritionService._estimate_nutrition")
@patch("src.nutrition.service.search_product")
@patch("src.nutrition.service.parse_food_text")
def test_usda_key_absent_skips_usda(mock_parse, mock_off, mock_llm):
    """No USDA key → USDA module never queried."""
    mock_parse.return_value = [ParsedFoodItem(name="rice", quantity=100, unit="g")]
    mock_off.return_value = None
    mock_llm.return_value = {"calories_per_100g": 130.0, "protein_per_100g": 2.0,
                             "fat_per_100g": 0.3, "carbs_per_100g": 28.0, "fiber_per_100g": 0.4}

    with patch("src.nutrition.usda.requests.get") as mock_usda:
        svc = NutritionService("fake-key")  # no usda_api_key
        svc.process_text("100g rice")
        mock_usda.assert_not_called()


@patch("src.nutrition.usda.search_product")
@patch("src.nutrition.service.search_product")
def test_get_nutrition_per_100g_uses_chain(mock_off, mock_usda):
    """get_nutrition_per_100g falls back to USDA when OFF misses."""
    mock_off.return_value = None
    mock_usda.return_value = _make_nutrition(kcal=389.0, protein=17.0, fat=7.0, carbs=66.0, fiber=10.0)

    svc = NutritionService("fake-key", usda_api_key="usda-key")
    result = svc.get_nutrition_per_100g("oats")

    assert result is not None
    assert result.calories_per_100g == pytest.approx(389.0)
    mock_usda.assert_called_once_with("oats", "usda-key")
