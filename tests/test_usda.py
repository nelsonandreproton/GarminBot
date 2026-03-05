"""Tests for src/nutrition/usda.py — USDA FoodData Central client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.nutrition.usda import search_product


def _mock_response(json_data: dict, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def _food_json(name="Brown Rice", kcal=111.0, protein=2.6, fat=0.9, carbs=23.0, fiber=1.8):
    """Build a USDA-style food entry with nutrient array."""
    return {
        "fdcId": 169704,
        "description": name,
        "dataType": "SR Legacy",
        "foodNutrients": [
            {"nutrientId": 1008, "value": kcal},    # calories
            {"nutrientId": 1003, "value": protein},  # protein
            {"nutrientId": 1004, "value": fat},      # fat
            {"nutrientId": 1005, "value": carbs},    # carbs
            {"nutrientId": 1079, "value": fiber},    # fiber
        ],
    }


@patch("src.nutrition.usda.requests.get")
def test_search_found(mock_get):
    mock_get.return_value = _mock_response({"foods": [_food_json("Brown Rice")]})

    result = search_product("brown rice", "test-key")

    assert result is not None
    assert result.product_name == "Brown Rice"
    assert result.calories_per_100g == 111.0
    assert result.protein_per_100g == 2.6
    assert result.fat_per_100g == 0.9
    assert result.carbs_per_100g == 23.0
    assert result.fiber_per_100g == 1.8
    assert result.serving_size_g is None  # USDA always per-100g


@patch("src.nutrition.usda.requests.get")
def test_search_no_results(mock_get):
    mock_get.return_value = _mock_response({"foods": []})

    result = search_product("produto inexistente xyz", "test-key")
    assert result is None


@patch("src.nutrition.usda.requests.get")
def test_search_skips_entries_without_calories(mock_get):
    """First result has no calories → skip to next with calories."""
    food_no_cal = {
        "fdcId": 1,
        "description": "No calories entry",
        "foodNutrients": [
            {"nutrientId": 1003, "value": 5.0},
        ],
    }
    food_with_cal = _food_json("Chicken Breast", kcal=165.0)
    mock_get.return_value = _mock_response({"foods": [food_no_cal, food_with_cal]})

    result = search_product("chicken", "test-key")

    assert result is not None
    assert result.calories_per_100g == 165.0


@patch("src.nutrition.usda.requests.get")
def test_network_error_returns_none(mock_get):
    import requests as req
    mock_get.side_effect = req.Timeout("timeout")

    result = search_product("anything", "test-key")
    assert result is None


@patch("src.nutrition.usda.requests.get")
def test_correct_api_key_passed(mock_get):
    """API key must be sent as query param, not in headers."""
    mock_get.return_value = _mock_response({"foods": [_food_json()]})
    search_product("rice", "my-usda-key")

    call_kwargs = mock_get.call_args
    params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params") or call_kwargs[0][1]
    assert params["api_key"] == "my-usda-key"
