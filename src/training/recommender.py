"""Workout recommender: generates a personalised gym session via Groq LLM."""

from __future__ import annotations

import logging
from datetime import date

from groq import Groq

logger = logging.getLogger(__name__)

_MODEL = "llama-3.3-70b-versatile"

_SYSTEM_PROMPT = """És um personal trainer especializado em perda de gordura e recomposição corporal.
Geras treinos de ginásio em português europeu, curtos e acionáveis.

REGRAS:
1. Estrutura cada treino em torno dos 5 padrões de movimento fundamentais:
   - SQUAT (agachamento): goblet squat, Bulgarian split squat, lunges, dumbbell squat
   - PUSH (empurrar): dumbbell bench press, floor press, overhead press, push-ups
   - PULL (puxar): pull-ups, dumbbell rows, band pull-aparts, inverted rows
   - HINGE (dobradiça): Romanian deadlift, hip thrust, kettlebell swing, good mornings
   - CARRY (transporte): farmer's walk, suitcase carry, overhead carry, waiter walk

2. Cada sessão inclui pelo menos 3 dos 5 padrões. Roda para cobrir todos ao longo da semana.

3. Usa APENAS o equipamento listado pelo utilizador. Não sugiras máquinas ou equipamento que não esteja na lista.

4. O treino deve caber no tempo indicado (inclui aquecimento de 5min e retorno à calma de 3min).

5. Adapta intensidade à recuperação:
   - Sono < 6h ou score < 50 → sessão leve, mobilidade + carries
   - Body battery < 20 → reduz volume, foca em 2-3 padrões
   - Stress alto (> 70) → evita HIIT, prefere trabalho steady-state
   - Boa recuperação → sessão completa com todos os 5 padrões

6. Considera a nutrição de ontem:
   - Calorias muito baixas ou proteína < 80g → reduz volume, mantém intensidade
   - Boa nutrição → sessão normal a intensa

7. Olha para o histórico dos últimos 7 dias e evita repetir os mesmos padrões em dias consecutivos.

8. Formato da resposta (Telegram, sem markdown pesado):
   🏋️ TREINO — [foco principal]
   ⏱ [X] minutos

   🔥 Aquecimento (5min)
   • [2-3 exercícios de mobilidade/ativação]

   [Para cada bloco de padrão:]
   [EMOJI] [PADRÃO] — [exercício]
   [séries] x [reps/tempo] | Descanso: [tempo]

   🧘 Retorno à calma (3min)
   • [alongamentos relevantes]

   💡 [Uma nota curta sobre intensidade/recuperação, se relevante]

9. Sê conciso. Não expliques o porquê de cada escolha. O utilizador quer um plano para seguir."""


def _build_user_prompt(
    metrics: dict,
    nutrition: dict | None,
    equipment: str,
    training_minutes: int,
    training_history: list[dict],
) -> str:
    """Build the user-facing prompt with concrete data from yesterday."""
    day = metrics.get("date", "?")
    sleep_h = metrics.get("sleep_hours")
    sleep_score = metrics.get("sleep_score")
    sleep_quality = metrics.get("sleep_quality", "—")
    steps = metrics.get("steps")
    active_cals = metrics.get("active_calories")
    rhr = metrics.get("resting_heart_rate")
    stress = metrics.get("avg_stress")
    bb_high = metrics.get("body_battery_high")
    bb_low = metrics.get("body_battery_low")
    weight = metrics.get("weight_kg")

    sleep_str = f"{sleep_h:.1f}h" if sleep_h is not None else "—"
    sleep_score_str = f"{sleep_score}/100" if sleep_score is not None else "—"
    steps_str = f"{steps:,}".replace(",", ".") if steps is not None else "—"
    active_cals_str = f"{active_cals} kcal" if active_cals is not None else "—"
    rhr_str = f"{rhr} bpm" if rhr is not None else "—"
    stress_str = f"{stress}/100" if stress is not None else "—"
    bb_str = f"{bb_low}–{bb_high}" if bb_low is not None and bb_high is not None else "—"
    weight_str = f"{weight:.1f} kg" if weight is not None else "—"

    if nutrition:
        nut_str = (
            f"{nutrition.get('calories', 0):.0f} kcal | "
            f"P: {nutrition.get('protein_g', 0):.0f}g | "
            f"G: {nutrition.get('fat_g', 0):.0f}g | "
            f"HC: {nutrition.get('carbs_g', 0):.0f}g"
        )
    else:
        nut_str = "Sem dados de nutrição"

    if training_history:
        history_lines = "\n".join(
            f"• {e['date']}: {e['description']}" for e in training_history
        )
    else:
        history_lines = "Nenhum treino registado"

    return (
        f"DADOS DE ONTEM ({day}):\n\n"
        f"😴 Sono: {sleep_str}, score {sleep_score_str}, qualidade: {sleep_quality}\n"
        f"👟 Passos: {steps_str}, calorias ativas: {active_cals_str}\n"
        f"❤️ FC repouso: {rhr_str}, stress médio: {stress_str}\n"
        f"🔋 Body battery: {bb_str}\n"
        f"⚖️ Peso: {weight_str}\n"
        f"🍽 Nutrição: {nut_str}\n\n"
        f"EQUIPAMENTO DISPONÍVEL:\n{equipment}\n\n"
        f"TEMPO DISPONÍVEL: {training_minutes} minutos\n\n"
        f"TREINOS ÚLTIMOS 7 DIAS:\n{history_lines}\n\n"
        f"Gera o treino para hoje."
    )


def generate_workout(
    metrics: dict,
    nutrition: dict | None,
    equipment: str,
    training_minutes: int,
    training_history: list[dict],
    api_key: str,
) -> str | None:
    """Generate a personalised workout via Groq.

    Args:
        metrics: DailyMetrics dict for yesterday (same format used by /sync).
        nutrition: Summed nutrition totals for yesterday, or None if no entries.
        equipment: Free-text description of available gym equipment.
        training_minutes: Available training time in minutes.
        training_history: List of {date, description} dicts from last 7 days.
        api_key: Groq API key.

    Returns:
        Formatted workout text ready to send via Telegram, or None on failure.
    """
    try:
        user_prompt = _build_user_prompt(
            metrics, nutrition, equipment, training_minutes, training_history
        )
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=_MODEL,
            max_tokens=800,
            temperature=0.7,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(
                lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            ).strip()
        return raw
    except Exception as exc:
        logger.warning("Workout generation failed: %s", exc)
        return None
