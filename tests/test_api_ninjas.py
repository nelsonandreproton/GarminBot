"""Tests for src/nutrition/api_ninjas.py — API-Ninjas Nutrition client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.nutrition.api_ninjas import search_product


def _mock_response(json_data, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def _food_item(
    name="banana",
    calories=89.4,
    protein=1.1,
    fat=0.3,
    carbs=23.0,
    fiber=2.6,
    serving_size_g=100.0,
):
    return {
        "name": name,
        "calories": calories,
        "protein_g": protein,
        "fat_total_g": fat,
        "carbohydrates_total_g": carbs,
        "fiber_g": fiber,
        "serving_size_g": serving_size_g,
    }


@patch("src.nutrition.api_ninjas.requests.get")
def test_search_found_per_100g(mock_get):
    """serving_size_g=100 means values are already per-100g."""
    mock_get.return_value = _mock_response([_food_item("banana", serving_size_g=100.0)])

    result = search_product("banana", "test-key")

    assert result is not None
    assert result.product_name == "banana"
    assert result.calories_per_100g == pytest.approx(89.4)
    assert result.protein_per_100g == pytest.approx(1.1)
    assert result.serving_size_g is None  # normalized


@patch("src.nutrition.api_ninjas.requests.get")
def test_search_normalizes_to_100g(mock_get):
    """serving_size_g != 100 → values are scaled to per-100g."""
    # 1 medium egg = 50g serving: 77 kcal per serving = 154 kcal/100g
    mock_get.return_value = _mock_response([
        _food_item("egg", calories=77.0, protein=6.3, fat=5.3, carbs=0.6, fiber=0.0, serving_size_g=50.0)
    ])

    result = search_product("egg", "test-key")

    assert result is not None
    assert result.calories_per_100g == pytest.approx(154.0)
    assert result.protein_per_100g == pytest.approx(12.6)
    assert result.fat_per_100g == pytest.approx(10.6)


@patch("src.nutrition.api_ninjas.requests.get")
def test_search_no_results(mock_get):
    mock_get.return_value = _mock_response([])

    result = search_product("produto inexistente", "test-key")
    assert result is None


@patch("src.nutrition.api_ninjas.requests.get")
def test_search_no_calories_returns_none(mock_get):
    """Item with no calorie data → None."""
    item = _food_item("mystery food", calories=None)
    item["calories"] = None
    mock_get.return_value = _mock_response([item])

    result = search_product("mystery food", "test-key")
    assert result is None


@patch("src.nutrition.api_ninjas.requests.get")
def test_network_error_returns_none(mock_get):
    import requests as req
    mock_get.side_effect = req.Timeout("timeout")

    result = search_product("anything", "test-key")
    assert result is None


@patch("src.nutrition.api_ninjas.requests.get")
def test_api_key_sent_as_header(mock_get):
    """API key must be sent as X-Api-Key header."""
    mock_get.return_value = _mock_response([_food_item()])
    search_product("banana", "my-ninjas-key")

    call_kwargs = mock_get.call_args
    headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
    assert headers.get("X-Api-Key") == "my-ninjas-key"


@patch("src.nutrition.api_ninjas.requests.get")
def test_zero_serving_size_falls_back_to_100(mock_get):
    """serving_size_g=0 should not cause division by zero."""
    item = _food_item(serving_size_g=0.0, calories=100.0)
    mock_get.return_value = _mock_response([item])

    result = search_product("weird food", "test-key")
    assert result is not None
    assert result.calories_per_100g == pytest.approx(100.0)
