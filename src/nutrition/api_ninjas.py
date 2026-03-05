"""API-Ninjas Nutrition API client: natural-language food name search.

Coverage: ~100k common/generic foods, quantity-aware NLP.
No barcode support. Values are normalized to per-100g.

API docs: https://api-ninjas.com/api/nutrition
Free tier: 10,000 calls/month. Register at https://api-ninjas.com/
"""

from __future__ import annotations

import logging

import requests

from .openfoodfacts import NutritionData

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.api-ninjas.com/v1/nutrition"
_TIMEOUT = 15


def search_product(query: str, api_key: str) -> NutritionData | None:
    """Search API-Ninjas Nutrition by name and return per-100g nutrition.

    Sends the raw query and normalizes the response to per-100g values
    using the serving_size_g field returned by the API.

    Args:
        query: Food name (e.g. "banana", "arroz cozido").
        api_key: API-Ninjas API key (X-Api-Key header).

    Returns:
        NutritionData with per-100g values, or None if not found / request fails.
    """
    try:
        resp = requests.get(
            _BASE_URL,
            headers={"X-Api-Key": api_key},
            params={"query": query},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        foods = resp.json()
    except requests.RequestException as exc:
        logger.warning("API-Ninjas nutrition search failed for '%s': %s", query, exc)
        return None

    if not foods:
        logger.info("API-Ninjas: no result for '%s'", query)
        return None

    # Take the first item (for a single food name query there is only one)
    food = foods[0]
    serving_g = float(food.get("serving_size_g") or 100.0)
    if serving_g <= 0:
        serving_g = 100.0

    # Normalize to per-100g
    factor = 100.0 / serving_g

    def _norm(key: str) -> float | None:
        val = food.get(key)
        if val is None:
            return None
        try:
            return round(float(val) * factor, 2)
        except (ValueError, TypeError):
            return None

    calories = _norm("calories")
    if calories is None:
        logger.info("API-Ninjas: result for '%s' has no calorie data", query)
        return None

    return NutritionData(
        product_name=food.get("name", query),
        calories_per_100g=calories,
        protein_per_100g=_norm("protein_g"),
        fat_per_100g=_norm("fat_total_g"),
        carbs_per_100g=_norm("carbohydrates_total_g"),
        fiber_per_100g=_norm("fiber_g"),
        serving_size_g=None,  # already normalized to per-100g
    )
