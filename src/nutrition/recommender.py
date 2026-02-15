"""Nutrition recommendation via Groq LLM based on daily data vs goals."""

from __future__ import annotations

import logging

from groq import Groq

logger = logging.getLogger(__name__)

_MODEL = "llama-3.3-70b-versatile"

_SYSTEM_PROMPT = """És um nutricionista desportivo. Com base nos dados de ontem e nos objetivos do utilizador, dá uma recomendação nutricional personalizada para hoje.

REGRAS:
- Sê conciso (máximo 4-5 frases)
- Foca-te no que é acionável para hoje
- Se houve défice de proteína, sugere alimentos ricos em proteína
- Se houve excesso calórico, sugere ajustes práticos
- Se a nutrição estava equilibrada, reforça positivamente
- Considera o nível de atividade física (calorias ativas do Garmin)
- Considera a qualidade do sono para sugestões de timing de refeições
- Se houver dados semanais, identifica padrões (ex: défice consistente de proteína)
- Responde em português
- Não uses formatação markdown (sem *, sem #, sem _)
- Usa emoji apenas no início da recomendação"""


def generate_nutrition_recommendation(
    nutrition: dict,
    goals: dict[str, float],
    metrics: dict | None = None,
    weekly_nutrition: dict | None = None,
    api_key: str = "",
) -> str | None:
    """Generate a personalized nutrition recommendation using the Groq LLM.

    Args:
        nutrition: Yesterday's nutrition totals (calories, protein_g, fat_g, carbs_g, fiber_g).
        goals: User goals dict (calories, protein_g, fat_g, carbs_g, etc.).
        metrics: Yesterday's Garmin health data or None.
        weekly_nutrition: Weekly average nutrition or None.
        api_key: Groq API key.

    Returns:
        Recommendation text ready for Telegram, or None on failure.
    """
    if not api_key:
        return None

    # Only generate if at least one macro goal is set
    macro_keys = ("calories", "protein_g", "fat_g", "carbs_g")
    macro_goals = {k: v for k, v in goals.items() if k in macro_keys}
    if not macro_goals:
        return None

    lines = ["Dados de ontem:"]

    cal = nutrition.get("calories") or 0
    prot = nutrition.get("protein_g") or 0
    fat = nutrition.get("fat_g") or 0
    carbs = nutrition.get("carbs_g") or 0
    fiber = nutrition.get("fiber_g") or 0
    lines.append(
        f"Ingerido: {int(cal)} kcal | P: {int(prot)}g | G: {int(fat)}g | HC: {int(carbs)}g | Fibra: {int(fiber)}g"
    )

    # Goals
    goal_parts: list[str] = []
    if "calories" in macro_goals:
        goal_parts.append(f"{int(macro_goals['calories'])} kcal")
    if "protein_g" in macro_goals:
        goal_parts.append(f"P: {int(macro_goals['protein_g'])}g")
    if "fat_g" in macro_goals:
        goal_parts.append(f"G: {int(macro_goals['fat_g'])}g")
    if "carbs_g" in macro_goals:
        goal_parts.append(f"HC: {int(macro_goals['carbs_g'])}g")
    lines.append(f"Objetivos: {' | '.join(goal_parts)}")

    # Deficit vs goals
    if "calories" in macro_goals:
        diff = int(cal - macro_goals["calories"])
        lines.append(f"Diferença calórica: {'+' if diff >= 0 else ''}{diff} kcal vs objetivo")

    # Garmin data
    if metrics:
        active = metrics.get("active_calories")
        resting = metrics.get("resting_calories")
        if active is not None and resting is not None:
            total_burned = active + resting
            deficit = total_burned - int(cal)
            lines.append(f"Calorias gastas (Garmin): {total_burned} kcal (défice real: {deficit} kcal)")

        sleep_h = metrics.get("sleep_hours")
        if sleep_h is not None:
            lines.append(f"Sono: {sleep_h:.1f}h")

        steps = metrics.get("steps")
        if steps is not None:
            lines.append(f"Passos: {steps}")

    # Weekly context
    if weekly_nutrition and weekly_nutrition.get("days_with_data", 0) > 0:
        avg_cal = weekly_nutrition.get("avg_calories", 0)
        avg_prot = weekly_nutrition.get("avg_protein", 0)
        lines.append(f"Média semanal: {int(avg_cal)} kcal/dia, {int(avg_prot)}g proteína/dia")

    user_prompt = "\n".join(lines)

    try:
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=_MODEL,
            max_tokens=300,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text_lines = text.splitlines()
            text = "\n".join(
                text_lines[1:-1] if text_lines[-1].strip() == "```" else text_lines[1:]
            ).strip()
        return text
    except Exception as exc:
        logger.error("Nutrition recommendation failed: %s", exc, exc_info=True)
        return None
