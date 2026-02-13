"""Tests for src/nutrition/parser.py — Claude API food text parser."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.nutrition.parser import ParsedFoodItem, parse_food_text


def _make_response(text: str):
    """Build a mock anthropic response object."""
    content_block = MagicMock()
    content_block.text = text
    msg = MagicMock()
    msg.content = [content_block]
    return msg


def _mock_client(response_text: str):
    client = MagicMock()
    client.messages.create.return_value = _make_response(response_text)
    return client


@patch("src.nutrition.parser.anthropic")
def test_parse_two_items(mock_anthropic):
    """Two items separated by 'e' are parsed correctly."""
    payload = json.dumps([
        {"name": "pudim continente proteína de chocolate", "quantity": 1, "unit": "un"},
        {"name": "mini babybel light", "quantity": 2, "unit": "un"},
    ])
    mock_anthropic.Anthropic.return_value = _mock_client(payload)

    result = parse_food_text("1 pudim continente +proteína de chocolate e 2 mini babybel light", "fake-key")

    assert len(result) == 2
    assert result[0].name == "pudim continente proteína de chocolate"
    assert result[0].quantity == 1.0
    assert result[0].unit == "un"
    assert result[1].name == "mini babybel light"
    assert result[1].quantity == 2.0


@patch("src.nutrition.parser.anthropic")
def test_parse_grams(mock_anthropic):
    """Weight in grams is parsed with unit='g'."""
    payload = json.dumps([{"name": "arroz cozido", "quantity": 150, "unit": "g"}])
    mock_anthropic.Anthropic.return_value = _mock_client(payload)

    result = parse_food_text("150g de arroz cozido", "fake-key")

    assert len(result) == 1
    assert result[0].quantity == 150.0
    assert result[0].unit == "g"
    assert result[0].name == "arroz cozido"


@patch("src.nutrition.parser.anthropic")
def test_parse_single_item_no_quantity(mock_anthropic):
    """Item without explicit quantity defaults to 1 un."""
    payload = json.dumps([{"name": "maçã", "quantity": 1, "unit": "un"}])
    mock_anthropic.Anthropic.return_value = _mock_client(payload)

    result = parse_food_text("uma maçã", "fake-key")

    assert len(result) == 1
    assert result[0].name == "maçã"
    assert result[0].quantity == 1.0
    assert result[0].unit == "un"


def test_empty_input_returns_empty_list():
    """Empty input returns empty list without calling API."""
    result = parse_food_text("", "fake-key")
    assert result == []


def test_whitespace_input_returns_empty_list():
    result = parse_food_text("   ", "fake-key")
    assert result == []


@patch("src.nutrition.parser.anthropic")
def test_invalid_json_raises_value_error(mock_anthropic):
    """Invalid JSON from Claude raises ValueError."""
    mock_anthropic.Anthropic.return_value = _mock_client("not json at all")

    with pytest.raises(ValueError, match="invalid JSON"):
        parse_food_text("something", "fake-key")


@patch("src.nutrition.parser.anthropic")
def test_parse_strips_markdown_code_fence(mock_anthropic):
    """JSON wrapped in ```json ... ``` is parsed correctly."""
    payload = json.dumps([{"name": "babybel light", "quantity": 2, "unit": "un"}])
    wrapped = f"```json\n{payload}\n```"
    mock_anthropic.Anthropic.return_value = _mock_client(wrapped)

    result = parse_food_text("2 babybel light", "fake-key")

    assert len(result) == 1
    assert result[0].name == "babybel light"
    assert result[0].quantity == 2.0
