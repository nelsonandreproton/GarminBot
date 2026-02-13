"""Barcode decoder: extract EAN/QR code from image bytes using pyzbar."""

from __future__ import annotations

import logging
from io import BytesIO

logger = logging.getLogger(__name__)

try:
    from PIL import Image
    from pyzbar.pyzbar import decode as pyzbar_decode
    _PYZBAR_AVAILABLE = True
except ImportError:
    _PYZBAR_AVAILABLE = False
    Image = None  # type: ignore[assignment]
    pyzbar_decode = None  # type: ignore[assignment]


def decode_barcode(image_bytes: bytes) -> str | None:
    """Decode the first barcode found in an image.

    Args:
        image_bytes: Raw image bytes (JPEG, PNG, etc).

    Returns:
        Barcode string (e.g. EAN-13) or None if not found or decode fails.
    """
    if not _PYZBAR_AVAILABLE or Image is None or pyzbar_decode is None:
        logger.error("pyzbar/Pillow not installed — barcode decoding unavailable")
        return None
    try:
        image = Image.open(BytesIO(image_bytes))
        barcodes = pyzbar_decode(image)
        if not barcodes:
            return None
        return barcodes[0].data.decode("utf-8")
    except Exception as exc:
        logger.warning("Barcode decode failed: %s", exc)
        return None
