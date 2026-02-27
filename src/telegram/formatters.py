"""Message formatters for Telegram: daily summaries, weekly reports, errors."""

from __future__ import annotations

from datetime import date
from typing import Any


def _fmt_hours(hours: float | None) -> str:
    """Format decimal hours as 'Xh YYmin'."""
    if hours is None:
        return "—"
    h = int(hours)
    m = int(round((hours - h) * 60))
    return f"{h}h {m:02d}min"


def _fmt_steps(steps: int | None) -> str:
    if steps is None:
        return "—"
    return f"{steps:,}".replace(",", ".")


def _fmt_cals(cals: int | None) -> str:
    if cals is None:
        return "—"
    return f"{cals:,}".replace(",", ".")


def _trend(current: float | None, average: float | None, unit: str = "") -> str:
    """Return a trend string like '(+1,060 passos)' or '(-8min)'."""
    if current is None or average is None:
        return ""
    diff = current - average
    sign = "+" if diff >= 0 else ""
    return f" ({sign}{diff:.0f}{unit})"


def _sleep_trend(current: float | None, average: float | None) -> str:
    """Return sleep trend in minutes, e.g. '(-8min)' or '(+12min)'."""
    if current is None or average is None:
        return ""
    diff_min = round((current - average) * 60)
    sign = "+" if diff_min >= 0 else ""
    return f" ({sign}{diff_min}min)"


def _day_name_pt(d: date) -> str:
    names = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
    return names[d.weekday()]


def format_daily_summary(
    metrics: dict[str, Any],
    weekly_stats: dict[str, Any] | None = None,
    alerts: list[str] | None = None,
    nutrition: dict[str, Any] | None = None,
    show_sleep: bool = True,
) -> str:
    """Format a daily health summary message for Telegram.

    Args:
        metrics: Dict with sleep_hours, sleep_score, sleep_quality,
                 steps, active_calories, resting_calories.
                 May include "nutrition" key with daily totals.
        weekly_stats: Optional 7-day averages for comparison section.
        alerts: Optional list of alert strings to append.
        nutrition: Optional daily nutrition totals (overrides metrics["nutrition"]).
        show_sleep: Whether to include the Sono section. Set to False for
                    today's live snapshot (/hoje), where sleep belongs to tomorrow.

    Returns:
        Markdown-formatted string ready to send via Telegram.
    """
    day: date = metrics.get("date", date.today())
    day_str = day.strftime("%d/%m/%Y")

    sleep_h = metrics.get("sleep_hours")
    sleep_score = metrics.get("sleep_score")
    sleep_quality = metrics.get("sleep_quality") or "—"
    steps = metrics.get("steps")
    active_cals = metrics.get("active_calories")
    resting_cals = metrics.get("resting_calories")

    lines = [f"📊 *Resumo de {day_str}*", ""]

    if show_sleep:
        score_stars = ""
        if sleep_score is not None:
            stars = min(5, max(0, round(sleep_score / 20)))
            score_stars = " " + "⭐" * stars
        sleep_lines = [
            "😴 *Sono*",
            f"• Duração: {_fmt_hours(sleep_h)}",
            f"• Score: {sleep_score if sleep_score is not None else '—'}/100{score_stars}",
            f"• Avaliação: {sleep_quality}",
        ]
        deep = metrics.get("sleep_deep_min")
        light = metrics.get("sleep_light_min")
        rem = metrics.get("sleep_rem_min")
        if any(v is not None for v in [deep, light, rem]):
            parts = []
            if deep is not None:
                parts.append(f"🔵 {deep}min profundo")
            if light is not None:
                parts.append(f"⚪ {light}min leve")
            if rem is not None:
                parts.append(f"🟣 {rem}min REM")
            sleep_lines.append("• Fases: " + " | ".join(parts))
        sleep_lines.append("")
        lines += sleep_lines

    floors = metrics.get("floors_ascended")
    int_mod = metrics.get("intensity_moderate_min")
    int_vig = metrics.get("intensity_vigorous_min")
    activity_lines = [
        "👟 *Atividade*",
        f"• Passos: {_fmt_steps(steps)}",
        f"• Calorias ativas: {_fmt_cals(active_cals)} kcal 🔥",
        f"• Calorias repouso: {_fmt_cals(resting_cals)} kcal",
    ]
    if floors is not None:
        activity_lines.append(f"• Andares: {floors} 🏢")
    if int_mod is not None or int_vig is not None:
        mod_str = f"{int_mod}min mod." if int_mod is not None else "—"
        vig_str = f"{int_vig}min vig." if int_vig is not None else "—"
        activity_lines.append(f"• Intensidade: {mod_str} + {vig_str}")
    lines += activity_lines

    rhr = metrics.get("resting_heart_rate")
    avg_stress = metrics.get("avg_stress")
    bb_high = metrics.get("body_battery_high")
    bb_low = metrics.get("body_battery_low")
    spo2 = metrics.get("spo2_avg")
    weight = metrics.get("weight_kg")
    if any(v is not None for v in [rhr, avg_stress, bb_high, bb_low, spo2, weight]):
        lines += ["", "❤️ *Saúde*"]
        if rhr is not None:
            lines.append(f"• FC repouso: {rhr} bpm")
        if avg_stress is not None:
            lines.append(f"• Stress médio: {avg_stress}/100")
        if bb_high is not None and bb_low is not None:
            lines.append(f"• Body Battery: {bb_low}–{bb_high}")
        if spo2 is not None:
            lines.append(f"• SpO2: {spo2:.1f}%")
        if weight is not None:
            lines.append(f"• Peso: {weight:.1f} kg")

    if weekly_stats:
        avg_sleep = weekly_stats.get("sleep_avg_hours")
        avg_steps = weekly_stats.get("steps_avg")
        steps_t = _trend(steps, avg_steps)

        weekly_lines = ["", "📈 *Comparação semanal:*"]
        if show_sleep:
            sleep_t = _sleep_trend(sleep_h, avg_sleep)
            weekly_lines.append(f"• Sono médio: {_fmt_hours(avg_sleep)}{sleep_t}")
        weekly_lines.append(f"• Passos médios: {_fmt_steps(avg_steps)}{steps_t}")
        lines += weekly_lines

    nutrition = nutrition or metrics.get("nutrition")
    if nutrition and nutrition.get("entry_count", 0) > 0:
        lines += ["", format_nutrition_summary({
            **nutrition,
            "active_calories": metrics.get("active_calories"),
            "resting_calories": metrics.get("resting_calories"),
            "total_calories": metrics.get("total_calories"),
        })]

    if alerts:
        lines += ["", "💬 *Alertas:*"] + [f"• {a}" for a in alerts]

    return "\n".join(lines)


def format_weekly_report(
    stats: dict[str, Any],
    prev_stats: dict[str, Any] | None = None,
    weekly_nutrition: dict[str, Any] | None = None,
    weight_stats: dict[str, Any] | None = None,
) -> str:
    """Format a 7-day summary message for Telegram.

    Args:
        stats: Dict from Repository.get_weekly_stats().
        prev_stats: Optional previous week stats for comparison.

    Returns:
        Markdown-formatted string.
    """
    start: date = stats.get("start_date")
    end: date = stats.get("end_date")

    if start and end:
        period = f"{start.strftime('%d')}-{end.strftime('%d %b')}"
    else:
        period = "últimos 7 dias"

    best_day = stats.get("sleep_best_day")
    worst_day = stats.get("sleep_worst_day")

    lines = [
        f"📅 *Relatório Semanal ({period})*",
        "",
        "😴 *Sono*",
        f"• Média: {_fmt_hours(stats.get('sleep_avg_hours'))}",
        f"• Melhor: {_fmt_hours(stats.get('sleep_best_hours'))}" + (f" ({_day_name_pt(best_day)})" if best_day else ""),
        f"• Pior: {_fmt_hours(stats.get('sleep_worst_hours'))}" + (f" ({_day_name_pt(worst_day)})" if worst_day else ""),
        f"• Score médio: {stats.get('sleep_avg_score', '—')}/100",
        "",
        "👟 *Atividade*",
        f"• Total passos: {_fmt_steps(stats.get('steps_total'))}",
        f"• Média diária: {_fmt_steps(stats.get('steps_avg'))}",
        f"• Calorias ativas: {_fmt_cals(stats.get('active_calories_total'))} kcal",
        f"• Calorias repouso: {_fmt_cals(stats.get('resting_calories_total'))} kcal",
    ]

    if prev_stats:
        prev_sleep = prev_stats.get("sleep_avg_hours")
        prev_steps = prev_stats.get("steps_avg")
        lines += ["", "📊 *vs semana anterior:*"]
        lines.append(f"• Sono: {_fmt_hours(stats.get('sleep_avg_hours'))}{_sleep_trend(stats.get('sleep_avg_hours'), prev_sleep)}")
        lines.append(f"• Passos médios: {_fmt_steps(stats.get('steps_avg'))}{_trend(stats.get('steps_avg'), prev_steps)}")

    if weight_stats and weight_stats.get("current_weight") is not None:
        lines += ["", format_weekly_weight(weight_stats)]

    if weekly_nutrition and weekly_nutrition.get("days_with_data", 0) > 0:
        lines += ["", format_weekly_nutrition(weekly_nutrition)]

    return "\n".join(lines)


def format_monthly_report(stats: dict[str, Any]) -> str:
    """Format a 30-day summary message for Telegram."""
    start: date = stats.get("start_date")
    end: date = stats.get("end_date")

    if start and end:
        period = f"{start.strftime('%d/%m')} – {end.strftime('%d/%m/%Y')}"
    else:
        period = "últimos 30 dias"

    lines = [
        f"📆 *Relatório Mensal ({period})*",
        f"_Dados de {stats.get('days_with_data', 0)} dias_",
        "",
        "😴 *Sono*",
        f"• Média: {_fmt_hours(stats.get('sleep_avg_hours'))}",
        "",
        "👟 *Atividade*",
        f"• Total passos: {_fmt_steps(stats.get('steps_total'))}",
        f"• Média diária: {_fmt_steps(stats.get('steps_avg'))}",
        f"• Calorias ativas: {_fmt_cals(stats.get('active_calories_total'))} kcal",
    ]

    return "\n".join(lines)


def format_error_message(context: str, error: Exception) -> str:
    """Format a user-friendly, actionable error notification.

    Maps known exception types to helpful guidance; falls back to a generic
    message for unknown errors.
    """
    import garminconnect

    type_name = type(error).__name__
    msg = str(error)[:200]

    if isinstance(error, garminconnect.GarminConnectAuthenticationError):
        detail = "Token expirado ou credenciais inválidas. Usa /sync para re-autenticar. Se persistir, verifica as credenciais no .env."
    elif isinstance(error, garminconnect.GarminConnectTooManyRequestsError):
        detail = "A API Garmin bloqueou temporariamente (demasiados pedidos). Tenta novamente em 15 minutos."
    elif isinstance(error, (ConnectionError, TimeoutError)) or "timeout" in msg.lower() or "connection" in msg.lower():
        detail = "Falha de rede. O bot vai tentar novamente automaticamente."
    elif "database" in type_name.lower() or "sqlalchemy" in type_name.lower():
        detail = "Erro na base de dados. Verifica os logs para mais detalhes."
    else:
        detail = f"`{type_name}: {msg}`"

    return f"⚠️ *Erro: {context}*\n{detail}"


def format_status(
    last_sync: Any | None,
    days_stored: int,
    recent_errors: list[Any],
    next_jobs: dict[str, str],
) -> str:
    """Format the /status command response.

    Args:
        last_sync: Last successful SyncLog entry (or None).
        days_stored: Total days in the database.
        recent_errors: List of recent SyncLog entries with errors.
        next_jobs: Dict mapping job name to next run time string.

    Returns:
        Markdown-formatted status message.
    """
    sync_time = last_sync.sync_date.strftime("%d/%m/%Y %H:%M") if last_sync else "Nunca"

    lines = [
        "🤖 *Status do Bot*",
        "",
        f"• Último sync bem-sucedido: {sync_time}",
        f"• Dias armazenados: {days_stored}",
    ]

    if recent_errors:
        lines += ["", "❌ *Erros recentes:*"]
        for log in recent_errors[:5]:
            ts = log.sync_date.strftime("%d/%m %H:%M")
            msg = (log.error_message or "erro desconhecido")[:80]
            lines.append(f"  • {ts}: {msg}")

    if next_jobs:
        lines += ["", "⏰ *Próximas execuções:*"]
        for name, run_time in next_jobs.items():
            lines.append(f"  • {name}: {run_time}")

    return "\n".join(lines)


def format_help_message() -> str:
    """Return the /ajuda command text listing all available commands."""
    return (
        "🤖 Comandos disponíveis:\n"
        "\n"
        "/hoje — Ponto de situação do dia atual (ao vivo)\n"
        "/ontem — Resumo de ontem\n"
        "/semana — Relatório semanal\n"
        "/mes — Relatório mensal\n"
        "/sync — Sincronizar e ver resumo do dia anterior\n"
        "/backfill N — Sincronizar últimos N dias\n"
        "/historico YYYY-MM-DD ou N — Ver dia ou últimos N dias\n"
        "/exportar N — Exportar dados em CSV\n"
        "/objetivo métrica valor — Ver ou definir objetivos (passos/sono/peso/calorias/proteina/gordura/hidratos)\n"
        "/peso [valor] — Ver ou registar peso\n"
        "/status — Estado do bot\n"
        "/comi texto — Registar refeição (ou nome de um preset)\n"
        "/nutricao — Resumo nutricional do dia\n"
        "/apagar — Apagar último alimento registado\n"
        "/preset create nome — Criar preset de refeição (interativo)\n"
        "/preset list — Listar presets guardados\n"
        "/preset delete nome — Apagar preset\n"
        "/ajuda — Esta mensagem"
    )


def format_history_table(rows: list[Any]) -> str:
    """Format a compact multi-day history table.

    Args:
        rows: List of DailyMetrics ORM objects ordered by date.

    Returns:
        Markdown-formatted string with one line per day.
    """
    lines = ["📋 *Histórico*", ""]
    for r in rows:
        day_str = r.date.strftime("%d/%m")
        day_name = _day_name_pt(r.date)[:3]
        sleep = _fmt_hours(r.sleep_hours)
        steps = _fmt_steps(r.steps)
        score = f"{r.sleep_score}/100" if r.sleep_score is not None else "—"
        lines.append(f"`{day_str}` {day_name} | 😴 {sleep} ({score}) | 👟 {steps}")
    return "\n".join(lines)


def calculate_deficit(
    active_cal: int | None,
    resting_cal: int | None,
    eaten_cal: float | None,
    total_cal: int | None = None,
) -> tuple[int | None, float | None]:
    """Calculate caloric deficit.

    Uses total_calories from Garmin when available (matches what the
    Garmin app displays).  Falls back to active + resting for old data.

    Positive = deficit (ate less than burned).
    Negative = surplus (ate more than burned).

    Returns:
        (deficit_kcal, deficit_pct) or (None, None) if data missing.
    """
    if eaten_cal is None or eaten_cal == 0:
        return None, None
    if total_cal is not None and total_cal > 0:
        total_burned = total_cal
    else:
        total_burned = (active_cal or 0) + (resting_cal or 0)
    if total_burned == 0:
        return None, None
    deficit = total_burned - int(eaten_cal)
    pct = round(deficit / total_burned * 100, 1)
    return deficit, pct


def format_nutrition_summary(nutrition: dict[str, Any]) -> str:
    """Format daily nutrition totals section for inclusion in daily report.

    Args:
        nutrition: Dict with calories, protein_g, fat_g, carbs_g, fiber_g,
                   and optionally active_calories, resting_calories.

    Returns:
        Markdown-formatted section string (without leading newline).
    """
    cal = nutrition.get("calories") or 0.0
    prot = nutrition.get("protein_g") or 0.0
    fat = nutrition.get("fat_g") or 0.0
    carbs = nutrition.get("carbs_g") or 0.0
    fiber = nutrition.get("fiber_g") or 0.0

    lines = [
        "🍽 *Nutrição*",
        f"• Calorias ingeridas: {int(cal)} kcal",
        f"• P: {int(prot)}g | G: {int(fat)}g | HC: {int(carbs)}g | Fibra: {int(fiber)}g",
    ]

    deficit, pct = calculate_deficit(
        nutrition.get("active_calories"),
        nutrition.get("resting_calories"),
        cal if cal > 0 else None,
        nutrition.get("total_calories"),
    )
    if deficit is not None and pct is not None:
        if deficit >= 0:
            lines.append(f"• Défice: -{deficit} kcal ({pct}%)")
        else:
            lines.append(f"• Excedente: +{abs(deficit)} kcal ({abs(pct)}%)")

    return "\n".join(lines)


def format_weekly_nutrition(weekly_nutrition: dict[str, Any]) -> str:
    """Format weekly average nutrition for the weekly report.

    Args:
        weekly_nutrition: Dict from Repository.get_weekly_nutrition(), optionally
                          enriched with an ``avg_deficit`` key (int kcal/day).
                          Positive avg_deficit = ate less than burned (deficit).
                          Negative avg_deficit = ate more than burned (surplus).

    Returns:
        Markdown-formatted section string.
    """
    avg_cal = weekly_nutrition.get("avg_calories") or 0.0
    avg_prot = weekly_nutrition.get("avg_protein") or 0.0
    avg_fat = weekly_nutrition.get("avg_fat") or 0.0
    avg_carbs = weekly_nutrition.get("avg_carbs") or 0.0
    avg_fiber = weekly_nutrition.get("avg_fiber") or 0.0
    days = weekly_nutrition.get("days_with_data", 0)
    avg_deficit = weekly_nutrition.get("avg_deficit")  # int | None

    lines = [
        "🍽 *Nutrição (média diária)*",
        f"• Calorias: {int(avg_cal)} kcal/dia",
        f"• P: {int(avg_prot)}g | G: {int(avg_fat)}g | HC: {int(avg_carbs)}g | Fibra: {int(avg_fiber)}g",
        f"• Dias com registo: {days}",
    ]
    if avg_deficit is not None:
        if avg_deficit >= 0:
            lines.append(f"• Défice médio: -{avg_deficit} kcal/dia")
        else:
            lines.append(f"• Excedente médio: +{abs(avg_deficit)} kcal/dia")
    return "\n".join(lines)


def format_weekly_weight(weight_stats: dict[str, Any]) -> str:
    """Format weekly weight section for the weekly report."""
    current = weight_stats.get("current_weight")
    current_date = weight_stats.get("current_date")
    delta = weight_stats.get("delta")
    min_w = weight_stats.get("min_weight")
    max_w = weight_stats.get("max_weight")

    lines = ["⚖️ *Peso*"]
    day_str = f" ({_day_name_pt(current_date)})" if current_date else ""
    lines.append(f"• Último registo: {current:.1f} kg{day_str}")
    if delta is not None:
        sign = "+" if delta > 0 else ""
        lines.append(f"• Variação: {sign}{delta:.1f} kg vs semana passada")
    if min_w is not None and max_w is not None and min_w != max_w:
        lines.append(f"• Intervalo: {min_w:.1f} – {max_w:.1f} kg")
    return "\n".join(lines)


def format_weight_status(
    current_weight: float | None,
    current_date: date | None,
    weight_stats: dict[str, Any] | None = None,
    goals: dict[str, float] | None = None,
    recent_records: list[tuple] | None = None,
) -> str:
    """Format the /peso command response with weekly stats and last 10 records."""
    if current_weight is None:
        return "⚖️ *Peso*\n\nSem registos de peso. Usa `/peso 78.5` para registar."

    day_str = current_date.strftime("%d/%m") if current_date else "—"
    lines = [
        "⚖️ *Peso — resumo*",
        "",
        f"• Atual: {current_weight:.1f} kg ({day_str})",
    ]

    if weight_stats:
        prev = weight_stats.get("prev_weight")
        delta = weight_stats.get("delta")
        if prev is not None and delta is not None:
            sign = "+" if delta > 0 else ""
            lines.append(f"• 7 dias atrás: {prev:.1f} kg")
            lines.append(f"• Variação: {sign}{delta:.1f} kg")

    weight_goal = (goals or {}).get("weight_kg")
    if weight_goal is not None:
        diff = current_weight - weight_goal
        if abs(diff) < 0.1:
            lines.append(f"• Objetivo: {weight_goal:.1f} kg — atingido! 🎯")
        elif diff > 0:
            lines.append(f"• Objetivo: {weight_goal:.1f} kg (faltam {diff:.1f} kg)")
        else:
            lines.append(f"• Objetivo: {weight_goal:.1f} kg ({abs(diff):.1f} kg abaixo)")

    if recent_records:
        lines.append("")
        lines.append("📋 *Últimos registos:*")
        for rec_date, rec_kg in recent_records:
            lines.append(f"  {rec_date.strftime('%d/%m/%Y')} — {rec_kg:.1f} kg")

    return "\n".join(lines)


def format_waist_status(
    recent_records: list[tuple],
) -> str:
    """Format the /barriga command response."""
    if not recent_records:
        return "📏 *Perímetro Abdominal*\n\nSem registos. Usa `/barriga 95.5` para registar (em cm)."

    lines = ["📏 *Perímetro Abdominal — últimos registos*", ""]
    first_cm = recent_records[-1][1] if len(recent_records) > 1 else None
    latest_cm = recent_records[0][1]

    for rec_date, rec_cm in recent_records:
        lines.append(f"  {rec_date.strftime('%d/%m/%Y')} — {rec_cm:.1f} cm")

    if first_cm is not None:
        delta = round(latest_cm - first_cm, 1)
        sign = "+" if delta > 0 else ""
        lines.append("")
        lines.append(f"• Variação no período: {sign}{delta:.1f} cm")

    return "\n".join(lines)


def format_food_confirmation(items: list[Any]) -> str:
    """Format a food item list as a Telegram confirmation message.

    Args:
        items: List of FoodItemResult objects.

    Returns:
        Markdown-formatted confirmation string with inline keyboard hint.
    """
    lines = ["📝 *Registar refeição:*", ""]
    total_cal = 0.0
    total_prot = 0.0
    total_fat = 0.0
    total_carbs = 0.0
    total_fiber = 0.0

    for i, item in enumerate(items, 1):
        qty_str = f"{int(item.quantity)}" if item.unit == "un" else f"{item.quantity:g}{item.unit}"
        cal = item.calories or 0.0
        prot = item.protein_g or 0.0
        fat = item.fat_g or 0.0
        carbs = item.carbs_g or 0.0
        fiber = item.fiber_g or 0.0
        total_cal += cal
        total_prot += prot
        total_fat += fat
        total_carbs += carbs
        total_fiber += fiber

        source_tag = " _(estimativa)_" if item.source == "llm_estimate" else ""
        lines.append(f"{i}. {item.name.title()} ({qty_str}){source_tag}")
        lines.append(f"   {int(cal)} kcal | P: {int(prot)}g | G: {int(fat)}g | HC: {int(carbs)}g | F: {int(fiber)}g")

    lines += [
        "",
        f"*Total: {int(total_cal)} kcal | P: {int(total_prot)}g | G: {int(total_fat)}g | HC: {int(total_carbs)}g | F: {int(total_fiber)}g*",
    ]
    return "\n".join(lines)


def format_nutrition_day(entries: list[Any], nutrition_totals: dict[str, Any],
                          garmin_metrics: Any | None = None) -> str:
    """Format full day nutrition view for /nutricao command.

    Args:
        entries: List of FoodEntry ORM objects for the day.
        nutrition_totals: Dict from Repository.get_daily_nutrition().
        garmin_metrics: Optional DailyMetrics ORM object for caloric balance.

    Returns:
        Markdown-formatted string.
    """
    today = date.today()
    day_str = today.strftime("%d/%m/%Y")

    lines = [f"🍽 *Nutrição — {day_str}*", ""]

    if entries:
        lines.append("📋 *Refeições registadas:*")
        for e in entries:
            time_str = e.created_at.strftime("%H:%M") if e.created_at else "—"
            qty_str = f"{int(e.quantity)}" if e.unit == "un" else f"{e.quantity:g}{e.unit}"
            cal = int(e.calories) if e.calories else "?"
            lines.append(f"• {time_str} — {e.name.title()} ({qty_str}) — {cal} kcal")
    else:
        lines.append("_Sem refeições registadas hoje._")
        lines.append("Usa /comi para registar.")
        return "\n".join(lines)

    cal = nutrition_totals.get("calories") or 0.0
    prot = nutrition_totals.get("protein_g") or 0.0
    fat = nutrition_totals.get("fat_g") or 0.0
    carbs = nutrition_totals.get("carbs_g") or 0.0
    fiber = nutrition_totals.get("fiber_g") or 0.0

    lines += [
        "",
        "📊 *Totais do dia:*",
        f"• Calorias: {int(cal)} kcal",
        f"• Proteína: {int(prot)}g | Gordura: {int(fat)}g | HC: {int(carbs)}g | Fibra: {int(fiber)}g",
    ]

    if garmin_metrics:
        active = garmin_metrics.active_calories
        resting = garmin_metrics.resting_calories
        total = getattr(garmin_metrics, "total_calories", None)
        deficit, pct = calculate_deficit(active, resting, cal if cal > 0 else None, total)
        if deficit is not None and pct is not None:
            total_burned = total if total and total > 0 else (active or 0) + (resting or 0)
            lines += [
                "",
                "⚖️ *Balanço calórico:*",
                f"• Gastas (Garmin): {total_burned:,} kcal".replace(",", "."),
                f"• Ingeridas: {int(cal)} kcal",
            ]
            if deficit >= 0:
                lines.append(f"• Défice: -{deficit} kcal ({pct}%)")
            else:
                lines.append(f"• Excedente: +{abs(deficit)} kcal ({abs(pct)}%)")
        elif active is None and resting is None:
            lines += ["", "⚖️ *Balanço calórico:* sem dados de atividade"]

    return "\n".join(lines)


def format_goals(goals: dict[str, float]) -> str:
    """Format current user goals for display."""
    steps = int(goals.get("steps", 10000))
    sleep_h = goals.get("sleep_hours", 7.0)
    weight_kg = goals.get("weight_kg")
    calories = goals.get("calories")
    protein_g = goals.get("protein_g")
    fat_g = goals.get("fat_g")
    carbs_g = goals.get("carbs_g")

    lines = [
        "🎯 *Objetivos atuais:*",
        "",
        f"• Passos diários: {steps:,}".replace(",", "."),
        f"• Sono mínimo: {_fmt_hours(sleep_h)}",
    ]
    if weight_kg is not None:
        lines.append(f"• Peso alvo: {weight_kg:.1f} kg")

    macro_lines: list[str] = []
    if calories is not None:
        macro_lines.append(f"• Calorias diárias: {int(calories)} kcal")
    if protein_g is not None:
        macro_lines.append(f"• Proteína: {int(protein_g)}g")
    if fat_g is not None:
        macro_lines.append(f"• Gordura: {int(fat_g)}g")
    if carbs_g is not None:
        macro_lines.append(f"• Hidratos: {int(carbs_g)}g")
    if macro_lines:
        lines += ["", "🍽 *Nutrição:*"] + macro_lines

    return "\n".join(lines)


def format_meal_preset_confirmation(preset_name: str, items: list[Any]) -> str:
    """Format a meal preset as a confirmation message (same style as food confirmation).

    Args:
        preset_name: The preset name (e.g. "Lanche").
        items: List of MealPresetItem ORM objects (or any object with the same attrs).

    Returns:
        Markdown-formatted confirmation string.
    """
    lines = [f"📝 *Registar preset \"{preset_name}\":*", ""]
    total_cal = 0.0
    total_prot = 0.0
    total_fat = 0.0
    total_carbs = 0.0
    total_fiber = 0.0

    for i, item in enumerate(items, 1):
        qty_str = f"{int(item.quantity)}" if item.unit == "un" else f"{item.quantity:g}{item.unit}"
        cal = item.calories or 0.0
        prot = item.protein_g or 0.0
        fat = item.fat_g or 0.0
        carbs = item.carbs_g or 0.0
        fiber = item.fiber_g or 0.0
        total_cal += cal
        total_prot += prot
        total_fat += fat
        total_carbs += carbs
        total_fiber += fiber
        lines.append(f"{i}. {item.name.title()} ({qty_str})")
        lines.append(f"   {int(cal)} kcal | P: {int(prot)}g | G: {int(fat)}g | HC: {int(carbs)}g | F: {int(fiber)}g")

    lines += [
        "",
        f"*Total: {int(total_cal)} kcal | P: {int(total_prot)}g | G: {int(total_fat)}g | HC: {int(total_carbs)}g | F: {int(total_fiber)}g*",
    ]
    return "\n".join(lines)


def format_meal_presets_list(presets: list[Any]) -> str:
    """Format the list of saved meal presets for /preset list.

    Args:
        presets: List of MealPreset ORM objects (with .name and .items loaded).

    Returns:
        Markdown-formatted string.
    """
    if not presets:
        return "📋 *Presets de refeição*\n\nSem presets guardados.\nUsa `/preset create <nome>` para criar um."

    lines = ["📋 *Presets de refeição:*", ""]
    for preset in presets:
        total_cal = sum((i.calories or 0) for i in preset.items)
        total_prot = sum((i.protein_g or 0) for i in preset.items)
        item_count = len(preset.items)
        lines.append(f"• *{preset.name}* — {item_count} item(s) | {int(total_cal)} kcal | P: {int(total_prot)}g")

    lines += ["", "_Usa /comi <nome> para registar um preset._"]
    lines.append("_Usa /preset delete <nome> para apagar._")
    return "\n".join(lines)


# Accepted suffixes for each macro field (case-insensitive)
_MACRO_PATTERNS = {
    "calories": ["cal", "kcal"],
    "protein_g": ["p", "prot", "proteina", "proteína"],
    "fat_g": ["g", "gord", "gordura"],
    "carbs_g": ["hc", "hidratos", "carbs", "c"],
    "fiber_g": ["f", "fibra", "fib"],
}


def parse_preset_item_line(line: str) -> dict | None:
    """Parse a single preset item line entered by the user.

    Accepted format:
        <qty> <name>: <value><suffix> <value><suffix> ...

    Suffixes (case-insensitive):
        calories  — cal, kcal
        protein_g — p, prot, proteína
        fat_g     — g, gord, gordura
        carbs_g   — hc, hidratos, carbs, c
        fiber_g   — f, fibra, fib

    Examples:
        "1 Pudim Proteína: 148cal 19p 3g 10hc 1f"
        "2 Babybell Light: 100kcal 12p 6g 0hc 0f"
        "1 Banana: 90cal 1p 0g 20hc 2f"

    Returns:
        Dict with keys name, quantity, unit, calories, protein_g, fat_g,
        carbs_g, fiber_g — or None if the line cannot be parsed.
    """
    import re
    line = line.strip()
    if not line:
        return None

    # Split on first colon: "<qty> <name>" : "<macros>"
    if ":" not in line:
        return None
    left, right = line.split(":", 1)

    # Parse qty + name from the left part
    left = left.strip()
    qty_match = re.match(r"^(\d+(?:[.,]\d+)?)\s+(.+)$", left)
    if qty_match:
        qty = float(qty_match.group(1).replace(",", "."))
        name = qty_match.group(2).strip()
    else:
        qty = 1.0
        name = left

    if not name:
        return None

    # Build a flat list of all suffix→field mappings (longest first to avoid
    # "kcal" being matched as "c" from carbs_g)
    suffix_map: list[tuple[str, str]] = []
    for field, suffixes in _MACRO_PATTERNS.items():
        for s in suffixes:
            suffix_map.append((s, field))
    suffix_map.sort(key=lambda x: len(x[0]), reverse=True)

    # Extract macro values from the right part
    macros: dict[str, float] = {}
    right = right.strip()
    # Find all "<number><suffix>" tokens (number may have decimal)
    tokens = re.findall(r"(\d+(?:[.,]\d+)?)\s*([a-záàâãéêíóôõúüçñ]+)", right, re.IGNORECASE)
    for num_str, suffix_raw in tokens:
        suffix = suffix_raw.lower()
        for pattern, field in suffix_map:
            if suffix == pattern:
                macros[field] = float(num_str.replace(",", "."))
                break

    # Require at least calories to be present
    if "calories" not in macros:
        return None

    return {
        "name": name,
        "quantity": qty,
        "unit": "un",
        "calories": macros.get("calories"),
        "protein_g": macros.get("protein_g"),
        "fat_g": macros.get("fat_g"),
        "carbs_g": macros.get("carbs_g"),
        "fiber_g": macros.get("fiber_g"),
    }


def format_remaining_macros(
    nutrition_totals: dict[str, Any],
    goals: dict[str, float],
    garmin_metrics: Any | None = None,
) -> str | None:
    """Format remaining macros to reach daily goals.

    Returns None if no macro goals are set.
    """
    cal_goal = goals.get("calories")
    prot_goal = goals.get("protein_g")
    fat_goal = goals.get("fat_g")
    carbs_goal = goals.get("carbs_g")

    if all(g is None for g in (cal_goal, prot_goal, fat_goal, carbs_goal)):
        return None

    eaten_cal = nutrition_totals.get("calories") or 0.0
    eaten_prot = nutrition_totals.get("protein_g") or 0.0
    eaten_fat = nutrition_totals.get("fat_g") or 0.0
    eaten_carbs = nutrition_totals.get("carbs_g") or 0.0

    parts: list[str] = []
    if cal_goal is not None:
        parts.append(f"{int(cal_goal - eaten_cal)} kcal")
    if prot_goal is not None:
        parts.append(f"P: {int(prot_goal - eaten_prot)}g")
    if fat_goal is not None:
        parts.append(f"G: {int(fat_goal - eaten_fat)}g")
    if carbs_goal is not None:
        parts.append(f"HC: {int(carbs_goal - eaten_carbs)}g")

    line = "🎯 Faltam: " + " | ".join(parts)

    # Add Garmin deficit info if calorie data available
    if garmin_metrics is not None and cal_goal is not None:
        if isinstance(garmin_metrics, dict):
            total = garmin_metrics.get("total_calories")
            active = garmin_metrics.get("active_calories")
            resting = garmin_metrics.get("resting_calories")
        else:
            total = getattr(garmin_metrics, "total_calories", None)
            active = getattr(garmin_metrics, "active_calories", None)
            resting = getattr(garmin_metrics, "resting_calories", None)
        if total is not None and total > 0:
            total_burned = total
        elif active is not None and resting is not None:
            total_burned = active + resting
        else:
            total_burned = None
        if total_burned is not None:
            balance = int(eaten_cal) - total_burned
            pct = round(abs(balance) / total_burned * 100, 1) if total_burned else 0.0
            if balance > 0:
                line += f"\n📊 Excedente vs Garmin: +{balance} kcal ({pct}%)"
            else:
                line += f"\n📊 Défice vs Garmin: {balance} kcal ({pct}%)"

    return line


def format_workout_section(workout_text: str) -> str:
    """Wrap LLM workout text with a Telegram-ready header."""
    return f"💪 *Treino de Hoje*\n\n{workout_text}"


# Activity type key → Portuguese label + emoji
_ACTIVITY_LABELS: dict[str, tuple[str, str]] = {
    "strength_training": ("Musculação", "🏋️"),
    "fitness_equipment": ("Ginásio", "🏋️"),
    "running": ("Corrida", "🏃"),
    "cycling": ("Ciclismo", "🚴"),
    "walking": ("Caminhada", "🚶"),
    "swimming": ("Natação", "🏊"),
    "yoga": ("Yoga", "🧘"),
    "cardio": ("Cardio", "❤️‍🔥"),
    "hiit": ("HIIT", "⚡"),
    "elliptical": ("Elíptica", "🔄"),
    "rowing": ("Remo", "🚣"),
    "pilates": ("Pilates", "🤸"),
    "hiking": ("Caminhada/Trekking", "🥾"),
    "tennis": ("Ténis", "🎾"),
    "basketball": ("Basquetebol", "🏀"),
    "football": ("Futebol", "⚽"),
    "indoor_cycling": ("Ciclismo Interior", "🚴"),
    "stair_climbing": ("Escadas", "🪜"),
    "open_water_swimming": ("Natação em Águas Abertas", "🌊"),
}


def format_activity_sync(activities: list[dict], day_label: str) -> str:
    """Format the result of /sync_atividades for Telegram.

    Args:
        activities: List of activity dicts with keys: name, type_key,
                    duration_min, calories, distance_km.
        day_label: Human-readable date label, e.g. "26/02/2026 (ontem)".
    """
    if not activities:
        return f"ℹ️ Sem atividades registadas no Garmin para {day_label}."

    lines = [f"✅ *Atividades sincronizadas — {day_label}*", ""]
    for act in activities:
        type_key = act.get("type_key", "unknown")
        label, emoji = _ACTIVITY_LABELS.get(type_key, (act.get("name", type_key), "🏅"))
        parts = []
        if act.get("duration_min") is not None:
            parts.append(f"{act['duration_min']} min")
        if act.get("calories") is not None:
            parts.append(f"{act['calories']} kcal")
        if act.get("distance_km") is not None:
            parts.append(f"{act['distance_km']} km")
        detail = " | ".join(parts) if parts else "—"
        lines.append(f"{emoji} *{label}* — {detail}")

    lines.append("")
    lines.append("💾 Guardado no registo de treino.")
    return "\n".join(lines)
