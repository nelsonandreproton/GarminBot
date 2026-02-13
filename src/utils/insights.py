"""Smart insights: detect patterns and milestones in Garmin data."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_STEPS_GOAL = 10_000
_DEFAULT_SLEEP_GOAL_H = 7.0


def generate_insights(rows: list[Any], goals: dict[str, float] | None = None) -> list[str]:
    """Analyse a list of DailyMetrics rows and return insight strings.

    Args:
        rows: List of DailyMetrics ORM objects ordered by date, most-recent last.
        goals: Optional dict with "steps" and "sleep_hours" targets.

    Returns:
        List of insight strings (may be empty).
    """
    if not rows:
        return []

    steps_goal = (goals or {}).get("steps", _DEFAULT_STEPS_GOAL)
    sleep_goal = (goals or {}).get("sleep_hours", _DEFAULT_SLEEP_GOAL_H)

    insights: list[str] = []
    steps_list = [r.steps for r in rows if r.steps is not None]
    sleep_list = [r.sleep_hours for r in rows if r.sleep_hours is not None]

    # --- Steps milestones ------------------------------------------------
    if steps_list:
        streak = _count_streak(rows, lambda r: r.steps is not None and r.steps >= steps_goal)
        if streak >= 7:
            insights.append(f"🏆 Incrível! {streak} dias consecutivos com ≥{int(steps_goal):,} passos!".replace(",", "."))
        elif streak >= 3:
            insights.append(f"🔥 {streak} dias consecutivos com ≥{int(steps_goal):,} passos — continua!".replace(",", "."))

        avg_steps = sum(steps_list) / len(steps_list)
        if avg_steps >= steps_goal:
            insights.append(f"👟 Média de passos acima do objetivo ({avg_steps:,.0f}) — excelente semana!".replace(",", "."))

    # --- Sleep patterns --------------------------------------------------
    if len(sleep_list) >= 5:
        weekend_sleep = [r.sleep_hours for r in rows if r.sleep_hours and r.date.weekday() >= 5]
        weekday_sleep = [r.sleep_hours for r in rows if r.sleep_hours and r.date.weekday() < 5]
        if weekend_sleep and weekday_sleep:
            wknd_avg = sum(weekend_sleep) / len(weekend_sleep)
            wkdy_avg = sum(weekday_sleep) / len(weekday_sleep)
            diff = wknd_avg - wkdy_avg
            if diff > 0.75:
                insights.append(f"😴 Dormes {diff:.1f}h mais ao fim-de-semana — padrão detetado.")

    # --- Declining steps trend -------------------------------------------
    if len(steps_list) >= 14:
        first_half = steps_list[:7]
        second_half = steps_list[7:]
        avg_first = sum(first_half) / len(first_half)
        avg_second = sum(second_half) / len(second_half)
        if avg_second < avg_first * 0.85:
            insights.append("📉 Atenção: atividade a diminuir nas últimas 2 semanas.")

    # --- Low sleep warning -----------------------------------------------
    if sleep_list:
        below_goal = sum(1 for h in sleep_list if h < sleep_goal)
        ratio = below_goal / len(sleep_list)
        if ratio >= 0.6:
            insights.append(f"⚠️ Mais de 60% das noites com menos de {sleep_goal:.1f}h de sono.")

    return insights


def generate_daily_alerts(metrics: dict[str, Any], rows: list[Any], goals: dict[str, float] | None = None) -> list[str]:
    """Generate contextual alerts to append to the daily report.

    Args:
        metrics: Today's metrics dict (sleep_hours, steps, sleep_score).
        rows: Recent DailyMetrics rows for streak detection.
        goals: Optional user goals dict.

    Returns:
        List of alert strings (may be empty).
    """
    steps_goal = (goals or {}).get("steps", _DEFAULT_STEPS_GOAL)
    sleep_goal = (goals or {}).get("sleep_hours", _DEFAULT_SLEEP_GOAL_H)

    alerts: list[str] = []
    sleep_h = metrics.get("sleep_hours")
    steps = metrics.get("steps")
    sleep_score = metrics.get("sleep_score")

    if sleep_h is not None and sleep_h < 6.0:
        alerts.append("⚠️ Dormiste pouco esta noite. Tenta descansar mais hoje.")
    elif sleep_score is not None and sleep_score >= 85:
        alerts.append("🌟 Excelente noite de sono!")

    if steps is not None and steps < 1000:
        alerts.append("🚶 Dia muito parado ontem. Tenta mexer-te hoje.")

    if rows and steps is not None and steps >= steps_goal:
        streak = _count_streak(rows, lambda r: r.steps is not None and r.steps >= steps_goal)
        if streak >= 5:
            alerts.append(f"🔥 {streak} dias seguidos acima do objetivo de passos!")

    return alerts


def _count_streak(rows: list[Any], condition: callable) -> int:
    """Count consecutive days (from most recent backwards) where condition is True."""
    streak = 0
    for row in reversed(rows):
        if condition(row):
            streak += 1
        else:
            break
    return streak
