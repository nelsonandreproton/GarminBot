"""Tests for src/nutrition/barcode.py — barcode decoding."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import src.nutrition.barcode as barcode_module
from src.nutrition.barcode import decode_barcode


def test_decode_barcode_found():
    """Valid barcode returns the code string."""
    barcode = MagicMock()
    barcode.data = b"3017620422003"

    mock_image_instance = MagicMock()
    mock_image_class = MagicMock()
    mock_image_class.open.return_value = mock_image_instance

    with (
        patch.object(barcode_module, "_PYZBAR_AVAILABLE", True),
        patch.object(barcode_module, "Image", mock_image_class),
        patch.object(barcode_module, "pyzbar_decode", return_value=[barcode]),
    ):
        result = decode_barcode(b"fake_image_bytes")

    assert result == "3017620422003"


def test_decode_barcode_not_found():
    """Image with no barcode returns None."""
    mock_image_instance = MagicMock()
    mock_image_class = MagicMock()
    mock_image_class.open.return_value = mock_image_instance

    with (
        patch.object(barcode_module, "_PYZBAR_AVAILABLE", True),
        patch.object(barcode_module, "Image", mock_image_class),
        patch.object(barcode_module, "pyzbar_decode", return_value=[]),
    ):
        result = decode_barcode(b"fake_no_barcode_image")

    assert result is None


def test_decode_corrupted_image_returns_none():
    """Corrupted/invalid image does not raise — returns None."""
    mock_image_class = MagicMock()
    mock_image_class.open.side_effect = Exception("Cannot identify image file")

    with (
        patch.object(barcode_module, "_PYZBAR_AVAILABLE", True),
        patch.object(barcode_module, "Image", mock_image_class),
        patch.object(barcode_module, "pyzbar_decode", return_value=[]),
    ):
        result = decode_barcode(b"corrupted")

    assert result is None


def test_decode_pyzbar_not_available():
    """Returns None gracefully when pyzbar is not installed."""
    with patch.object(barcode_module, "_PYZBAR_AVAILABLE", False):
        result = decode_barcode(b"any_bytes")
    assert result is None
