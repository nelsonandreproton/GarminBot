"""Tests for src/nutrition/openfoodfacts.py — OpenFoodFacts API client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.nutrition.openfoodfacts import NutritionData, lookup_barcode, search_product


def _mock_response(json_data: dict, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def _product_json(name="Test Product", kcal=150.0, protein=10.0, fat=5.0, carbs=20.0, fiber=2.0, serving=30.0):
    return {
        "status": 1,
        "product": {
            "product_name": name,
            "serving_quantity": serving,
            "nutriments": {
                "energy-kcal_100g": kcal,
                "proteins_100g": protein,
                "fat_100g": fat,
                "carbohydrates_100g": carbs,
                "fiber_100g": fiber,
            },
        },
    }


@patch("src.nutrition.openfoodfacts.requests.get")
def test_lookup_barcode_found(mock_get):
    mock_get.return_value = _mock_response(_product_json("Babybel Light"))

    result = lookup_barcode("3017620422003")

    assert result is not None
    assert result.product_name == "Babybel Light"
    assert result.calories_per_100g == 150.0
    assert result.protein_per_100g == 10.0
    assert result.serving_size_g == 30.0


@patch("src.nutrition.openfoodfacts.requests.get")
def test_lookup_barcode_not_found(mock_get):
    mock_get.return_value = _mock_response({"status": 0}, status_code=404)
    mock_get.return_value.raise_for_status = MagicMock()

    result = lookup_barcode("0000000000000")
    assert result is None


@patch("src.nutrition.openfoodfacts.requests.get")
def test_lookup_barcode_status_zero(mock_get):
    mock_get.return_value = _mock_response({"status": 0})

    result = lookup_barcode("1234567890123")
    assert result is None


@patch("src.nutrition.openfoodfacts.requests.get")
def test_search_product_found(mock_get):
    mock_get.return_value = _mock_response({
        "products": [_product_json("Arroz Cozido")["product"]]
    })

    result = search_product("arroz cozido")

    assert result is not None
    assert result.product_name == "Arroz Cozido"
    assert result.calories_per_100g == 150.0


@patch("src.nutrition.openfoodfacts.requests.get")
def test_search_product_no_results(mock_get):
    mock_get.return_value = _mock_response({"products": []})

    result = search_product("produto inexistente xyz123")
    assert result is None


@patch("src.nutrition.openfoodfacts.requests.get")
def test_network_timeout_returns_none(mock_get):
    import requests as req
    mock_get.side_effect = req.Timeout("timeout")

    result = lookup_barcode("1234567890123")
    assert result is None

    result2 = search_product("qualquer coisa")
    assert result2 is None
