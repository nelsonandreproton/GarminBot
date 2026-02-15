"""NutritionService: orchestrates parsing, lookup, fallback, and nutrient calculation."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from groq import Groq

from .barcode import decode_barcode
from .openfoodfacts import NutritionData, lookup_barcode, search_product
from .parser import ParsedFoodItem, parse_food_text

logger = logging.getLogger(__name__)

_ESTIMATE_SYSTEM = """Tu és um nutricionista. Dado o nome de um alimento, estima os valores nutricionais por 100g.
Responde APENAS com JSON válido, sem markdown:
{"calories_per_100g": N, "protein_per_100g": N, "fat_per_100g": N, "carbs_per_100g": N, "fiber_per_100g": N}
Usa valores típicos para o produto. Se for uma unidade (ex: ovo, banana), estima para 100g do alimento."""

_MODEL = "llama-3.3-70b-versatile"


@dataclass
class FoodItemResult:
    name: str
    quantity: float
    unit: str
    calories: float | None
    protein_g: float | None
    fat_g: float | None
    carbs_g: float | None
    fiber_g: float | None
    source: str  # "openfoodfacts" | "llm_estimate" | "barcode"
    barcode: str | None = None


class NutritionService:
    """Orchestrates food parsing, nutritional lookup, and fallback estimation."""

    def __init__(self, groq_api_key: str) -> None:
        self._api_key = groq_api_key

    def process_text(self, text: str) -> list[FoodItemResult]:
        """Parse free text and look up nutrition for each item.

        Args:
            text: Portuguese free-form text (e.g. "2 ovos e 150g de arroz").

        Returns:
            List of FoodItemResult with nutritional data populated.
        """
        parsed: list[ParsedFoodItem] = parse_food_text(text, self._api_key)
        results = []
        for item in parsed:
            nutrition = search_product(item.name)
            if nutrition:
                source = "openfoodfacts"
            else:
                logger.info("OFF not found for '%s', using LLM estimate", item.name)
                nutrition = self._estimate_as_nutrition_data(item.name)
                source = "llm_estimate"

            nutrients = self._calculate_nutrients(nutrition, item.quantity, item.unit) if nutrition else {}
            results.append(FoodItemResult(
                name=item.name,
                quantity=item.quantity,
                unit=item.unit,
                calories=nutrients.get("calories"),
                protein_g=nutrients.get("protein_g"),
                fat_g=nutrients.get("fat_g"),
                carbs_g=nutrients.get("carbs_g"),
                fiber_g=nutrients.get("fiber_g"),
                source=source,
            ))
        return results

    def process_barcode(self, image_bytes: bytes) -> FoodItemResult | None:
        """Decode barcode from image and look up nutrition.

        Args:
            image_bytes: Raw image bytes from Telegram photo.

        Returns:
            FoodItemResult or None if barcode not found or product not in OFF.
        """
        code = decode_barcode(image_bytes)
        if not code:
            logger.info("No barcode detected in image")
            return None

        nutrition = lookup_barcode(code)
        if not nutrition:
            logger.info("Barcode %s not found in OpenFoodFacts", code)
            return None

        nutrients = self._calculate_nutrients(nutrition, 1.0, "un")
        return FoodItemResult(
            name=nutrition.product_name,
            quantity=1.0,
            unit="un",
            calories=nutrients.get("calories"),
            protein_g=nutrients.get("protein_g"),
            fat_g=nutrients.get("fat_g"),
            carbs_g=nutrients.get("carbs_g"),
            fiber_g=nutrients.get("fiber_g"),
            source="barcode",
            barcode=code,
        )

    def _estimate_as_nutrition_data(self, food_name: str) -> NutritionData | None:
        """Ask LLM to estimate nutritional values per 100g."""
        raw = self._estimate_nutrition(food_name)
        if not raw:
            return None
        return NutritionData(
            product_name=food_name,
            calories_per_100g=raw.get("calories_per_100g"),
            protein_per_100g=raw.get("protein_per_100g"),
            fat_per_100g=raw.get("fat_per_100g"),
            carbs_per_100g=raw.get("carbs_per_100g"),
            fiber_per_100g=raw.get("fiber_per_100g"),
            serving_size_g=None,
        )

    def _estimate_nutrition(self, food_name: str) -> dict:
        """Fallback: ask LLM to estimate nutritional values per 100g.

        Returns:
            Dict with keys calories_per_100g, protein_per_100g, fat_per_100g,
            carbs_per_100g, fiber_per_100g. Empty dict on failure.
        """
        try:
            client = Groq(api_key=self._api_key)
            response = client.chat.completions.create(
                model=_MODEL,
                max_tokens=200,
                messages=[
                    {"role": "system", "content": _ESTIMATE_SYSTEM},
                    {"role": "user", "content": food_name},
                ],
            )
            raw = response.choices[0].message.content.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                lines = raw.splitlines()
                raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
            return json.loads(raw)
        except Exception as exc:
            logger.warning("LLM nutrition estimate failed for '%s': %s", food_name, exc)
            return {}

    def _calculate_nutrients(
        self, nutrition: NutritionData, quantity: float, unit: str
    ) -> dict:
        """Calculate total nutrients for a given quantity and unit.

        Args:
            nutrition: Per-100g nutritional data.
            quantity: Amount consumed.
            unit: "g", "ml", or "un".

        Returns:
            Dict with keys: calories, protein_g, fat_g, carbs_g, fiber_g.
        """
        if unit in ("g", "ml"):
            # Direct weight — scale from per-100g
            factor = quantity / 100.0
        elif unit == "un":
            # Use serving size if available, otherwise assume 100g
            serving = nutrition.serving_size_g or 100.0
            factor = (serving * quantity) / 100.0
        else:
            factor = quantity / 100.0

        def _scale(val: float | None) -> float | None:
            if val is None:
                return None
            return round(val * factor, 1)

        return {
            "calories": _scale(nutrition.calories_per_100g),
            "protein_g": _scale(nutrition.protein_per_100g),
            "fat_g": _scale(nutrition.fat_per_100g),
            "carbs_g": _scale(nutrition.carbs_per_100g),
            "fiber_g": _scale(nutrition.fiber_per_100g),
        }
