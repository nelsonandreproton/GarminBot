"""USDA FoodData Central API client: name-based nutrition search.

Coverage: strong for generic/raw ingredients and US-branded foods.
No barcode support. Values returned are always per-100g.

API docs: https://fdc.nal.usda.gov/api-guide.html
Free API key: https://fdc.nal.usda.gov/api-key-signup.html
"""

from __future__ import annotations

import logging

import requests

from .openfoodfacts import NutritionData

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
_TIMEOUT = 15

# Nutrient IDs for USDA FoodData Central
_ID_CALORIES = 1008  # ENERC_KCAL
_ID_PROTEIN = 1003   # PROCNT
_ID_FAT = 1004       # FAT
_ID_CARBS = 1005     # CHOCDF
_ID_FIBER = 1079     # FIBTG


def search_product(query: str, api_key: str) -> NutritionData | None:
    """Search USDA FoodData Central by name and return per-100g nutrition.

    Args:
        query: Food name (e.g. "brown rice", "chicken breast").
        api_key: USDA FDC API key.

    Returns:
        NutritionData with per-100g values, or None if not found / request fails.
    """
    try:
        resp = requests.get(
            _BASE_URL,
            params={
                "query": query,
                "dataType": "Branded,Foundation,SR Legacy",
                "pageSize": 5,
                "api_key": api_key,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        foods = resp.json().get("foods", [])
    except requests.RequestException as exc:
        logger.warning("USDA FDC search failed for '%s': %s", query, exc)
        return None

    for food in foods:
        nutrient_map: dict[int, float] = {}
        for n in food.get("foodNutrients", []):
            nid = n.get("nutrientId")
            val = n.get("value")
            if nid is not None and val is not None:
                nutrient_map[int(nid)] = float(val)

        calories = nutrient_map.get(_ID_CALORIES)
        if calories is None:
            continue  # skip entries with no caloric data

        return NutritionData(
            product_name=food.get("description", query),
            calories_per_100g=calories,
            protein_per_100g=nutrient_map.get(_ID_PROTEIN),
            fat_per_100g=nutrient_map.get(_ID_FAT),
            carbs_per_100g=nutrient_map.get(_ID_CARBS),
            fiber_per_100g=nutrient_map.get(_ID_FIBER),
            serving_size_g=None,  # USDA values are always per-100g
        )

    logger.info("USDA FDC: no usable result for '%s'", query)
    return None
