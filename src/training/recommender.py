"""Workout recommendation via Groq LLM based on health data and equipment."""

from __future__ import annotations

import logging

from groq import Groq

logger = logging.getLogger(__name__)

_MODEL = "llama-3.3-70b-versatile"

_SYSTEM_PROMPT = """És um personal trainer focado em perda de peso. Gera um plano de treino de ginásio para hoje.

REGRAS:
- Estrutura os exercícios em torno de 5 padrões de movimento: Squat, Push, Pull, Hinge, Carry
- Cada sessão deve incluir pelo menos 3 dos 5 padrões; roda para que todos sejam cobertos ao longo da semana
- Usa APENAS o equipamento listado
- Cumpre o limite de tempo indicado
- Adapta intensidade com base na qualidade do sono e sinais de recuperação
- Considera a nutrição do dia anterior para disponibilidade energética

EXEMPLOS POR PADRÃO:
- Squat: goblet squat, dumbbell squat, Bulgarian split squat, lunges
- Push: dumbbell bench press, floor press, overhead press, push-ups
- Pull: dumbbell rows, band pull-aparts, face pulls
- Hinge: dumbbell Romanian deadlift, hip thrust, good mornings
- Carry: farmer's walk, suitcase carry, overhead carry

RECUPERAÇÃO:
- Sono < 6h ou score < 50 → sessão mais leve ou recuperação ativa (mobilidade + carries)
- Body battery < 20 → reduzir volume, focar em 2-3 padrões apenas
- Stress alto → preferir cardio estável em vez de HIIT, carries mais leves
- Bom sono + boa nutrição → sessão completa com todos os 5 padrões

FORMATO:
- Para cada exercício: padrão de movimento, nome do exercício, séries x repetições, tempo de descanso
- Inclui aquecimento breve e cooldown
- Mantém curto e acionável (mensagem Telegram)
- Responde em português
- Não uses formatação markdown (sem *, sem #, sem _)
- Usa emoji apenas nos cabeçalhos de secção"""


def generate_workout(
    metrics: dict,
    nutrition: dict | None,
    equipment: str,
    training_minutes: int,
    api_key: str,
    weekday: int | None = None,
) -> str | None:
    """Generate a workout recommendation using the Groq LLM.

    Args:
        metrics: Yesterday's health data (sleep_hours, sleep_score, steps, etc.).
        nutrition: Yesterday's nutrition totals or None.
        equipment: Free-text equipment list.
        training_minutes: Max session duration in minutes.
        api_key: Groq API key.
        weekday: Day of week (0=Monday). If None, uses current day.

    Returns:
        Workout text ready for Telegram, or None on failure.
    """
    if weekday is None:
        from datetime import date
        weekday = date.today().weekday()

    day_names = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
    day_name = day_names[weekday]

    # Build user prompt with concrete data
    lines = [f"Dia: {day_name}"]

    sleep_h = metrics.get("sleep_hours")
    sleep_score = metrics.get("sleep_score")
    if sleep_h is not None:
        lines.append(f"Sono: {sleep_h:.1f}h (score: {sleep_score}/100)" if sleep_score else f"Sono: {sleep_h:.1f}h")

    steps = metrics.get("steps")
    if steps is not None:
        lines.append(f"Passos ontem: {steps}")

    bb_high = metrics.get("body_battery_high")
    bb_low = metrics.get("body_battery_low")
    if bb_high is not None and bb_low is not None:
        lines.append(f"Body battery: {bb_low}-{bb_high}")

    stress = metrics.get("avg_stress")
    if stress is not None:
        lines.append(f"Stress médio: {stress}/100")

    if nutrition and nutrition.get("calories"):
        cal = int(nutrition["calories"])
        prot = int(nutrition.get("protein_g") or 0)
        lines.append(f"Nutrição ontem: {cal} kcal, {prot}g proteína")

    lines.append(f"\nEquipamento disponível: {equipment}")
    lines.append(f"Tempo disponível: {training_minutes} minutos")

    user_prompt = "\n".join(lines)

    try:
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=_MODEL,
            max_tokens=600,
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
        logger.error("Workout generation failed: %s", exc, exc_info=True)
        return None
