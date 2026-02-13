"""Claude API-based food text parser: Portuguese free text → structured items."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import anthropic

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Tu és um parser de alimentos. Recebe texto em Português que descreve o que alguém comeu.
Extrai cada alimento individual com quantidade e unidade.

Regras:
- "e" separa alimentos diferentes
- "+" faz parte do nome do mesmo produto (ex: "+proteína" é parte do produto)
- Se não há quantidade explícita, assume 1 unidade
- Se há peso (ex: "150g"), usa unit="g"
- Se há volume (ex: "200ml"), usa unit="ml"
- Caso contrário, usa unit="un"
- Normaliza nomes: remove "de", "um/uma" desnecessários no início, mantém marca e variante

Responde APENAS com JSON válido, sem markdown:
[{"name": "...", "quantity": N, "unit": "..."}]"""


@dataclass
class ParsedFoodItem:
    name: str
    quantity: float
    unit: str  # "un" | "g" | "ml"


def parse_food_text(text: str, api_key: str) -> list[ParsedFoodItem]:
    """Parse Portuguese free text into a list of structured food items.

    Args:
        text: Free-form Portuguese text describing food eaten.
        api_key: Anthropic API key.

    Returns:
        List of ParsedFoodItem. Empty list if input is empty or parse fails.

    Raises:
        ValueError: If Claude returns invalid JSON.
        anthropic.APIError: On API-level errors.
    """
    text = text.strip()
    if not text:
        return []

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )

    raw = response.content[0].text.strip()
    logger.debug("Parser raw response: %s", raw)

    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned invalid JSON: {raw!r}") from exc

    if not isinstance(items, list):
        raise ValueError(f"Expected JSON array, got: {type(items)}")

    result = []
    for item in items:
        if not isinstance(item, dict) or "name" not in item:
            logger.warning("Skipping malformed item: %s", item)
            continue
        result.append(ParsedFoodItem(
            name=str(item["name"]),
            quantity=float(item.get("quantity", 1.0)),
            unit=str(item.get("unit", "un")),
        ))

    return result
