"""OpenFoodFacts API client: barcode lookup and product search."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "GarminBot/1.0"}
_TIMEOUT = 10


@dataclass
class NutritionData:
    product_name: str
    calories_per_100g: float | None
    protein_per_100g: float | None
    fat_per_100g: float | None
    carbs_per_100g: float | None
    fiber_per_100g: float | None
    serving_size_g: float | None


def _parse_nutriments(data: dict) -> NutritionData:
    """Extract NutritionData from an OpenFoodFacts product dict."""
    nutriments = data.get("nutriments", {})
    product_name = (
        data.get("product_name_pt")
        or data.get("product_name")
        or data.get("abbreviated_product_name")
        or "Produto desconhecido"
    )
    serving_raw = data.get("serving_quantity") or data.get("serving_size")
    serving_g: float | None = None
    if serving_raw:
        try:
            serving_g = float(serving_raw)
        except (ValueError, TypeError):
            pass

    return NutritionData(
        product_name=product_name,
        calories_per_100g=_safe_float(nutriments.get("energy-kcal_100g")),
        protein_per_100g=_safe_float(nutriments.get("proteins_100g")),
        fat_per_100g=_safe_float(nutriments.get("fat_100g")),
        carbs_per_100g=_safe_float(nutriments.get("carbohydrates_100g")),
        fiber_per_100g=_safe_float(nutriments.get("fiber_100g")),
        serving_size_g=serving_g,
    )


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def lookup_barcode(barcode: str) -> NutritionData | None:
    """Look up nutritional data for a product by barcode (EAN-13 etc).

    Args:
        barcode: Product barcode string.

    Returns:
        NutritionData or None if not found or request fails.
    """
    url = f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != 1:
            return None
        return _parse_nutriments(data.get("product", {}))
    except requests.RequestException as exc:
        logger.warning("OpenFoodFacts barcode lookup failed: %s", exc)
        return None


def search_product(query: str) -> NutritionData | None:
    """Search OpenFoodFacts for a product by name.

    Args:
        query: Product name to search for.

    Returns:
        NutritionData for the first match, or None if not found.
    """
    url = "https://world.openfoodfacts.org/cgi/search.pl"
    params = {
        "search_terms": query,
        "json": "1",
        "page_size": "1",
        "countries_tags": "pt",
    }
    try:
        resp = requests.get(url, headers=_HEADERS, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        products = data.get("products", [])
        if not products:
            return None
        return _parse_nutriments(products[0])
    except requests.RequestException as exc:
        logger.warning("OpenFoodFacts search failed: %s", exc)
        return None
