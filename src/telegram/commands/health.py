"""Health command handlers: /hoje, /ontem, /semana, /mes, /historico."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..helpers import _is_rate_limited, _row_to_metrics, safe_command

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class HealthMixin:
    """Mixin providing health/dashboard command handlers."""

    async def _cmd_hoje(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/hoje — live snapshot of today from Garmin (no sleep — assigned tomorrow)."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        if self._garmin_client is None:
            await update.message.reply_text("Garmin não configurado.")
            return
        await update.message.reply_text("⏳ A obter dados de hoje do Garmin...")
        today = date.today()
        try:
            activity = self._garmin_client.get_activity_data(today)
        except Exception as exc:
            logger.error("Failed to fetch today's activity: %s", exc)
            from ..formatters import format_error_message
            await update.message.reply_text(format_error_message("dados de hoje", exc), parse_mode=ParseMode.MARKDOWN)
            return
        try:
            health = self._garmin_client.get_health_data(today)
        except Exception as exc:
            logger.warning("Failed to fetch today's health data: %s", exc)
            health = {}
        metrics: dict[str, Any] = {"date": today}
        if activity:
            metrics["steps"] = activity.steps
            metrics["active_calories"] = activity.active_calories
            metrics["resting_calories"] = activity.resting_calories
            metrics["total_calories"] = activity.total_calories
        metrics.update(health)
        nutrition = self._repo.get_daily_nutrition(today)
        if nutrition.get("entry_count", 0) > 0:
            metrics["nutrition"] = {
                **nutrition,
                "active_calories": metrics.get("active_calories"),
                "resting_calories": metrics.get("resting_calories"),
                "total_calories": metrics.get("total_calories"),
            }
        await self.send_daily_summary(metrics, show_sleep=False)

    @safe_command
    async def _cmd_ontem(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/ontem — yesterday's full summary (same as /hoje but with sleep)."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        yesterday = date.today() - timedelta(days=1)
        row = self._repo.get_metrics_by_date(yesterday)
        if row is None:
            await update.message.reply_text("Sem dados para ontem. Tenta /sync primeiro.")
            return
        metrics = _row_to_metrics(row)
        nutrition = self._repo.get_daily_nutrition(yesterday)
        if nutrition.get("entry_count", 0) > 0:
            metrics["nutrition"] = {
                **nutrition,
                "active_calories": metrics.get("active_calories"),
                "resting_calories": metrics.get("resting_calories"),
                "total_calories": metrics.get("total_calories"),
            }
        await self.send_daily_summary(metrics)

    @safe_command
    async def _cmd_semana(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/semana — weekly report for the previous Mon–Sun week."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return

        # Calculate the most recent completed Mon–Sun week.
        # weekday(): Mon=0 … Sun=6
        today = date.today()
        days_since_monday = today.weekday()  # 0 on Monday, 6 on Sunday
        last_sunday = today - timedelta(days=days_since_monday + 1)
        last_monday = last_sunday - timedelta(days=6)

        stats = self._repo.get_weekly_stats(last_sunday)
        if not stats:
            await update.message.reply_text(
                f"Sem dados suficientes para a semana de {last_monday} a {last_sunday}."
            )
            return

        # Fetch rows early — needed for deficit calculation AND chart
        from ...utils.charts import generate_weekly_chart
        from ...utils.insights import generate_insights
        rows = self._repo.get_metrics_range(last_monday, last_sunday)

        # Compute per-day caloric deficit (burned - eaten; None if no food data)
        deficits: list[int | None] = []
        for row in rows:
            nutrition = self._repo.get_daily_nutrition(row.date)
            if nutrition.get("entry_count", 0) == 0:
                deficits.append(None)
                continue
            eaten = nutrition.get("calories") or 0.0
            burned = row.total_calories or ((row.active_calories or 0) + (row.resting_calories or 0))
            deficits.append(int(burned) - int(eaten) if burned else None)

        # Enrich weekly_nutrition with avg_deficit before passing to the report
        weekly_nutrition = self._repo.get_weekly_nutrition(last_sunday)
        if weekly_nutrition:
            valid = [d for d in deficits if d is not None]
            weekly_nutrition["avg_deficit"] = round(sum(valid) / len(valid)) if valid else None

        weight_stats = self._repo.get_weekly_weight_stats(last_sunday)
        water_avg = self._repo.get_weekly_water_avg(last_sunday)
        await self.send_weekly_report(stats, weight_stats=weight_stats or None, weekly_nutrition=weekly_nutrition, water_weekly_avg_ml=water_avg)

        # Chart
        if rows:
            goals = self._repo.get_goals()
            chart_bytes = generate_weekly_chart(rows, goals=goals, deficits=deficits)
            if chart_bytes:
                await self.send_image(chart_bytes, caption="📊 Evolução semanal")

            all_rows = self._repo.get_metrics_range(last_sunday - timedelta(days=13), last_sunday)
            insights = generate_insights(all_rows, goals=goals)
            if insights:
                insight_text = "💡 *Insights:*\n" + "\n".join(f"• {i}" for i in insights)
                await self._send(insight_text)

        # Training load from Garmin activities
        from ..formatters import format_weekly_training_load
        training_load = self._repo.get_weekly_training_load(last_sunday)
        if training_load:
            load_text = format_weekly_training_load(training_load)
            if load_text:
                await self._send(load_text)

    @safe_command
    async def _cmd_mes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/mes — last 30 days stats + chart."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        yesterday = date.today() - timedelta(days=1)
        stats = self._repo.get_monthly_stats(yesterday)
        if not stats:
            await update.message.reply_text("Sem dados suficientes para o mês.")
            return
        from ..formatters import format_monthly_report
        await update.message.reply_text(format_monthly_report(stats), parse_mode=ParseMode.MARKDOWN)
        # Send monthly chart
        from ...utils.charts import generate_monthly_chart
        start = stats.get("start_date", yesterday - timedelta(days=29))
        rows = self._repo.get_metrics_range(start, yesterday)
        if rows:
            goals = self._repo.get_goals()
            chart = generate_monthly_chart(rows, goals=goals)
            if chart:
                await self.send_image(chart, caption="📈 Tendência mensal")

    async def _send_yesterday_report(self) -> None:
        """Build and send yesterday's daily report. Used by /sync (async context)."""
        from ..formatters import format_error_message
        yesterday = date.today() - timedelta(days=1)
        row = self._repo.get_metrics_by_date(yesterday)
        if row is None:
            await self.send_error(
                f"relatório de {yesterday}",
                RuntimeError("Sem dados para ontem — o sync falhou ou ainda não correu."),
            )
            self._repo.log_report_sent()
            return
        metrics = _row_to_metrics(row)
        nutrition = self._repo.get_daily_nutrition(yesterday)
        if nutrition.get("entry_count", 0) > 0:
            metrics["nutrition"] = {
                **nutrition,
                "active_calories": row.active_calories,
                "resting_calories": row.resting_calories,
                "total_calories": row.total_calories,
            }
        await self.send_daily_summary(metrics)
        self._repo.log_report_sent()
        logger.info("Daily report sent for %s (via /sync)", yesterday)

    @safe_command
    async def _cmd_historico(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/historico <YYYY-MM-DD> or /historico <N> — specific day or last N days."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        from ..formatters import format_daily_summary, format_history_table
        import datetime as _dt

        args = context.args or []
        arg = args[0].strip() if args else ""

        if not arg:
            await update.message.reply_text("Uso: /historico YYYY-MM-DD  ou  /historico <N dias>")
            return

        # Try N days mode
        if arg.isdigit():
            n = int(arg)
            if not 1 <= n <= 14:
                await update.message.reply_text("Número de dias deve ser entre 1 e 14.")
                return
            end = date.today() - timedelta(days=1)
            start = end - timedelta(days=n - 1)
            rows = self._repo.get_metrics_range(start, end)
            if not rows:
                await update.message.reply_text("Sem dados para esse período.")
                return
            await update.message.reply_text(format_history_table(rows), parse_mode=ParseMode.MARKDOWN)
            return

        # Try date mode
        try:
            target = _dt.date.fromisoformat(arg)
        except ValueError:
            await update.message.reply_text("Formato inválido. Usa YYYY-MM-DD ou um número de dias.")
            return
        if target > date.today():
            await update.message.reply_text("Não posso mostrar dados futuros.")
            return
        if (date.today() - target).days > 90:
            await update.message.reply_text("Máximo de 90 dias atrás.")
            return
        row = self._repo.get_metrics_by_date(target)
        if row is None:
            await update.message.reply_text(f"Sem dados para {target.strftime('%d/%m/%Y')}. Tenta /backfill.")
            return
        metrics = _row_to_metrics(row)
        await update.message.reply_text(format_daily_summary(metrics), parse_mode=ParseMode.MARKDOWN)
