"""Telegram bot: message sending and command handlers."""

from __future__ import annotations

import asyncio
import csv
import io as _io
import logging
import time
import warnings
from datetime import date, timedelta
from typing import Any, Callable

from telegram import Bot, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.warnings import PTBUserWarning
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

warnings.filterwarnings("ignore", message="per_message=False", category=PTBUserWarning)

from ..config import Config
from ..database.repository import Repository
from .formatters import (
    format_activity_sync,
    format_daily_summary,
    format_error_message,
    format_food_confirmation,
    format_meal_preset_confirmation,
    format_meal_presets_list,
    format_monthly_report,
    format_nutrition_day,
    format_status,
    format_waist_status,
    format_weekly_report,
    format_weight_status,
    format_workout_section,
    parse_preset_item_line,
)

# ConversationHandler states
_AWAITING_CONFIRMATION = 0
_AWAITING_BARCODE_QUANTITY = 1
_AWAITING_PRESET_ITEMS = 2

logger = logging.getLogger(__name__)

# Rate limiting: max 1 command per N seconds per chat
_RATE_LIMIT_SECONDS = 3
_last_command_time: dict[int, float] = {}


def _is_rate_limited(chat_id: int) -> bool:
    now = time.monotonic()
    last = _last_command_time.get(chat_id, 0.0)
    if now - last < _RATE_LIMIT_SECONDS:
        return True
    _last_command_time[chat_id] = now
    return False


def _parse_date_prefix(args: list[str]) -> tuple[date, list[str]]:
    """Parse an optional date prefix from command args.

    Recognised prefixes (case-insensitive):
      ontem          → yesterday
      anteontem      → two days ago
      YYYY-MM-DD     → exact date
      DD/MM/YYYY     → exact date (pt format)

    Returns (resolved_date, remaining_args).
    Raises ValueError if a date keyword is recognised but the date is invalid or in the future.
    """
    today = date.today()
    if not args:
        return today, args

    first = args[0].lower()

    if first == "ontem":
        return today - timedelta(days=1), args[1:]

    if first == "anteontem":
        return today - timedelta(days=2), args[1:]

    # Try YYYY-MM-DD
    if len(first) == 10 and first[4] == "-" and first[7] == "-":
        try:
            parsed = date.fromisoformat(args[0])
        except ValueError:
            raise ValueError(f"Data inválida: {args[0]}")
        if parsed > today:
            raise ValueError("Não posso registar em datas futuras.")
        return parsed, args[1:]

    # Try DD/MM/YYYY
    if len(first) == 10 and first[2] == "/" and first[5] == "/":
        try:
            day, month, year = first.split("/")
            parsed = date(int(year), int(month), int(day))
        except (ValueError, IndexError):
            raise ValueError(f"Data inválida: {args[0]}")
        if parsed > today:
            raise ValueError("Não posso registar em datas futuras.")
        return parsed, args[1:]

    return today, args


def _row_to_metrics(row: Any) -> dict[str, Any]:
    """Convert a DailyMetrics ORM row to a flat dict for formatters."""
    return {
        "date": row.date,
        "sleep_hours": row.sleep_hours,
        "sleep_score": row.sleep_score,
        "sleep_quality": row.sleep_quality,
        "sleep_deep_min": getattr(row, "sleep_deep_min", None),
        "sleep_light_min": getattr(row, "sleep_light_min", None),
        "sleep_rem_min": getattr(row, "sleep_rem_min", None),
        "sleep_awake_min": getattr(row, "sleep_awake_min", None),
        "steps": row.steps,
        "active_calories": row.active_calories,
        "resting_calories": row.resting_calories,
        "total_calories": row.total_calories,
        "floors_ascended": getattr(row, "floors_ascended", None),
        "intensity_moderate_min": getattr(row, "intensity_moderate_min", None),
        "intensity_vigorous_min": getattr(row, "intensity_vigorous_min", None),
        "resting_heart_rate": row.resting_heart_rate,
        "avg_stress": row.avg_stress,
        "body_battery_high": row.body_battery_high,
        "body_battery_low": row.body_battery_low,
        "spo2_avg": getattr(row, "spo2_avg", None),
        "weight_kg": row.weight_kg,
    }


def _on_send_retry(retry_state) -> None:
    logger.warning("Telegram send attempt %d failed: %s", retry_state.attempt_number, retry_state.outcome.exception())


class TelegramBot:
    """Wraps python-telegram-bot for sending messages and handling commands."""

    def __init__(self, config: Config, repository: Repository, garmin_sync_callback: Callable | None = None, garmin_backfill_callback: Callable | None = None, garmin_client=None) -> None:
        self._config = config
        self._repo = repository
        self._chat_id = int(config.telegram_chat_id)
        self._garmin_sync = garmin_sync_callback
        self._garmin_backfill = garmin_backfill_callback
        self._garmin_client = garmin_client
        self._garmin_report: Callable | None = None  # Set by main.py after bot creation
        self._app: Application | None = None
        # NutritionService (lazy init — only if GROQ_API_KEY is set)
        self._nutrition_service = None
        if config.groq_api_key:
            from ..nutrition.service import NutritionService
            self._nutrition_service = NutritionService(config.groq_api_key)

    # ------------------------------------------------------------------ #
    # Sending                                                               #
    # ------------------------------------------------------------------ #

    @retry(
        retry=retry_if_exception_type(TelegramError),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        before_sleep=_on_send_retry,
        reraise=True,
    )
    async def _send(self, text: str, chat_id: int | None = None) -> None:
        """Send a Markdown message to the configured chat."""
        bot = Bot(token=self._config.telegram_bot_token)
        await bot.send_message(
            chat_id=chat_id or self._chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
        )

    async def send_daily_summary(
        self,
        metrics: dict[str, Any],
        show_sleep: bool = True,
    ) -> None:
        """Fetch weekly context, generate alerts, and send the daily summary message."""
        day = metrics.get("date", date.today())
        weekly = self._repo.get_weekly_stats(day)
        alerts: list[str] = []
        if self._config.daily_alerts:
            from ..utils.insights import generate_daily_alerts
            goals = self._repo.get_goals()
            recent_rows = self._repo.get_metrics_range(day - timedelta(days=6), day)
            alerts = generate_daily_alerts(metrics, recent_rows, goals)
        text = format_daily_summary(
            metrics,
            weekly_stats=weekly,
            alerts=alerts or None,
            show_sleep=show_sleep,
        )
        await self._send(text)
        logger.info("Daily summary sent")

    async def send_weekly_report(
        self,
        stats: dict[str, Any],
        weight_stats: dict[str, Any] | None = None,
        weekly_nutrition: dict[str, Any] | None = None,
    ) -> None:
        """Send the weekly report message with week-over-week comparison, weight, and nutrition.

        Args:
            stats: Weekly stats dict from Repository.get_weekly_stats().
            weight_stats: Optional weekly weight stats.
            weekly_nutrition: Pre-computed weekly nutrition dict (with optional avg_deficit).
                              If None, it is fetched from the repository.
        """
        end_date = stats.get("end_date")
        prev_stats = self._repo.get_previous_weekly_stats(end_date) if end_date else None
        if weekly_nutrition is None:
            weekly_nutrition = self._repo.get_weekly_nutrition(end_date) if end_date else None
        text = format_weekly_report(
            stats,
            prev_stats=prev_stats,
            weekly_nutrition=weekly_nutrition if weekly_nutrition and weekly_nutrition.get("days_with_data", 0) > 0 else None,
            weight_stats=weight_stats,
        )
        await self._send(text)
        logger.info("Weekly report sent")


    async def send_error(self, context: str, error: Exception) -> None:
        """Send an error notification to the configured chat."""
        try:
            await self._send(format_error_message(context, error))
        except Exception as exc:
            logger.error("Failed to send error notification: %s", exc)

    # ------------------------------------------------------------------ #
    # Command handlers                                                      #
    # ------------------------------------------------------------------ #

    def _auth_check(self, update: Update) -> bool:
        """Return True if the message is from the authorized chat."""
        if update.effective_chat is None:
            return False
        return update.effective_chat.id == self._chat_id

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
        from ..utils.charts import generate_weekly_chart
        from ..utils.insights import generate_insights
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
        await self.send_weekly_report(stats, weight_stats=weight_stats or None, weekly_nutrition=weekly_nutrition)

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

    async def _cmd_mes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/mes — last 30 days stats + chart."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        yesterday = date.today() - timedelta(days=1)
        stats = self._repo.get_monthly_stats(yesterday)
        if not stats:
            await update.message.reply_text("Sem dados suficientes para o mês.")
            return
        await update.message.reply_text(format_monthly_report(stats), parse_mode=ParseMode.MARKDOWN)
        # Send monthly chart
        from ..utils.charts import generate_monthly_chart
        start = stats.get("start_date", yesterday - timedelta(days=29))
        rows = self._repo.get_metrics_range(start, yesterday)
        if rows:
            goals = self._repo.get_goals()
            chart = generate_monthly_chart(rows, goals=goals)
            if chart:
                await self.send_image(chart, caption="📈 Tendência mensal")

    async def _send_yesterday_report(self) -> None:
        """Build and send yesterday's daily report. Used by /sync (async context)."""
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

    async def _cmd_sync(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sync — sync yesterday's Garmin data and send the daily summary."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        if self._garmin_sync is None:
            await update.message.reply_text("Sync não configurado.")
            return
        await update.message.reply_text("⏳ A sincronizar com o Garmin Connect...")
        try:
            self._garmin_sync()
        except Exception as exc:
            logger.error("Manual sync failed: %s", exc)
            await update.message.reply_text(format_error_message("sync manual", exc), parse_mode=ParseMode.MARKDOWN)
            return
        # Send yesterday's report directly (already in async context — no _run_async needed)
        await self._send_yesterday_report()

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/status — bot status and last sync info."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        last_sync = self._repo.get_last_successful_sync()
        days_stored = self._repo.count_stored_days()
        recent_errors = [
            log for log in self._repo.get_recent_sync_logs(10)
            if log.status in ("error", "partial")
        ]

        # Fetch next job run times from scheduler if available
        next_jobs: dict[str, str] = {}
        if context.bot_data.get("scheduler"):
            scheduler = context.bot_data["scheduler"]
            for job in scheduler.get_jobs():
                next_run = job.next_run_time
                if next_run:
                    next_jobs[job.name] = next_run.strftime("%d/%m %H:%M")

        text = format_status(last_sync, days_stored, recent_errors, next_jobs)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_ajuda(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/ajuda — list all commands."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        from .formatters import format_help_message
        await update.message.reply_text(format_help_message())

    async def _cmd_historico(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/historico <YYYY-MM-DD> or /historico <N> — specific day or last N days."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        from .formatters import format_daily_summary, format_history_table
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

    async def _cmd_exportar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/exportar [N|nutricao] — export Garmin or nutrition data as CSV."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        args = context.args or []

        # /exportar nutricao
        if args and args[0].lower() == "nutricao":
            from ..database.models import FoodEntry
            today = date.today()
            start = today - timedelta(days=90)
            entries = self._repo.get_food_entries_range(start, today)
            if not entries:
                await update.message.reply_text("Sem dados de nutrição para exportar.")
                return
            buf = _io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["data", "nome", "quantidade", "unidade", "calorias",
                             "proteina_g", "gordura_g", "hidratos_g", "fibra_g", "fonte", "barcode"])
            for e in entries:
                writer.writerow([e.date, e.name, e.quantity, e.unit, e.calories,
                                 e.protein_g, e.fat_g, e.carbs_g, e.fiber_g, e.source, e.barcode])
            filename = f"nutricao_export_{start}_{today}.csv"
            csv_bytes = buf.getvalue().encode("utf-8")
            bot = Bot(token=self._config.telegram_bot_token)
            await bot.send_document(
                chat_id=self._chat_id,
                document=InputFile(_io.BytesIO(csv_bytes), filename=filename),
                caption=f"🥗 {len(entries)} registos de nutrição exportados",
            )
            return

        limit = None
        if args and args[0].isdigit():
            limit = int(args[0])

        rows = self._repo.get_all_metrics(limit_days=limit)
        if not rows:
            await update.message.reply_text("Sem dados para exportar.")
            return

        buf = _io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["data", "sono_horas", "sono_score", "sono_qualidade", "passos",
                         "calorias_ativas", "calorias_repouso", "fc_repouso", "stress_medio",
                         "body_battery_max", "body_battery_min"])
        for r in rows:
            writer.writerow([
                r.date, r.sleep_hours, r.sleep_score, r.sleep_quality,
                r.steps, r.active_calories, r.resting_calories,
                r.resting_heart_rate, r.avg_stress, r.body_battery_high, r.body_battery_low,
            ])

        filename = f"garmin_export_{rows[0].date}_{rows[-1].date}.csv"
        csv_bytes = buf.getvalue().encode("utf-8")
        bot = Bot(token=self._config.telegram_bot_token)
        await bot.send_document(
            chat_id=self._chat_id,
            document=InputFile(_io.BytesIO(csv_bytes), filename=filename),
            caption=f"📊 {len(rows)} dias exportados",
        )

    async def _cmd_backfill(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/backfill <N> — sync last N missing days (max 30)."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        if self._garmin_backfill is None:
            await update.message.reply_text("Garmin sync não configurado.")
            return
        args = context.args or []
        n = int(args[0]) if args and args[0].isdigit() else 7
        n = min(n, 30)
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=n - 1)
        missing = self._repo.get_missing_dates(start, end)
        if not missing:
            await update.message.reply_text(f"✅ Sem dias em falta nos últimos {n} dias.")
            return
        await update.message.reply_text(f"⏳ A sincronizar {len(missing)} dias em falta...")
        self._garmin_backfill(missing)
        await update.message.reply_text(f"✅ Backfill concluído para {len(missing)} dias.")

    async def _cmd_objetivo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/objetivo [passos|sono <valor>] — view or set goals."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        from .formatters import format_goals
        args = context.args or []

        if not args:
            goals = self._repo.get_goals()
            await update.message.reply_text(format_goals(goals), parse_mode=ParseMode.MARKDOWN)
            return

        if len(args) < 2:
            await update.message.reply_text(
                "Uso: /objetivo <métrica> <valor>\n"
                "Métricas: passos, sono, peso, calorias, proteina, gordura, hidratos"
            )
            return

        metric_arg = args[0].lower()
        try:
            value = float(args[1].replace(",", "."))
        except ValueError:
            await update.message.reply_text("Valor inválido. Usa um número (ex: 8000 ou 7.5).")
            return

        if metric_arg in ("passos", "steps"):
            if value <= 0:
                await update.message.reply_text("Objetivo de passos deve ser > 0.")
                return
            self._repo.set_goal("steps", value)
            await update.message.reply_text(f"✅ Objetivo de passos definido: {int(value):,}".replace(",", "."))
        elif metric_arg in ("sono", "sleep"):
            if not 0 < value <= 24:
                await update.message.reply_text("Objetivo de sono deve ser entre 0 e 24 horas.")
                return
            self._repo.set_goal("sleep_hours", value)
            h = int(value)
            m = int(round((value - h) * 60))
            await update.message.reply_text(f"✅ Objetivo de sono definido: {h}h {m:02d}min")
        elif metric_arg in ("peso", "weight"):
            if not 20 < value < 300:
                await update.message.reply_text("Objetivo de peso deve ser entre 20 e 300 kg.")
                return
            self._repo.set_goal("weight_kg", value)
            await update.message.reply_text(f"✅ Objetivo de peso definido: {value:.1f} kg")
        elif metric_arg in ("calorias", "calories", "kcal", "cal"):
            if not 500 <= value <= 10000:
                await update.message.reply_text("Objetivo de calorias deve ser entre 500 e 10000 kcal.")
                return
            self._repo.set_goal("calories", value)
            await update.message.reply_text(f"✅ Objetivo de calorias definido: {int(value)} kcal")
        elif metric_arg in ("proteina", "proteinas", "protein"):
            if not 10 <= value <= 500:
                await update.message.reply_text("Objetivo de proteína deve ser entre 10 e 500g.")
                return
            self._repo.set_goal("protein_g", value)
            await update.message.reply_text(f"✅ Objetivo de proteína definido: {int(value)}g")
        elif metric_arg in ("gordura", "fat"):
            if not 10 <= value <= 300:
                await update.message.reply_text("Objetivo de gordura deve ser entre 10 e 300g.")
                return
            self._repo.set_goal("fat_g", value)
            await update.message.reply_text(f"✅ Objetivo de gordura definido: {int(value)}g")
        elif metric_arg in ("hidratos", "carbs", "hc"):
            if not 20 <= value <= 800:
                await update.message.reply_text("Objetivo de hidratos deve ser entre 20 e 800g.")
                return
            self._repo.set_goal("carbs_g", value)
            await update.message.reply_text(f"✅ Objetivo de hidratos definido: {int(value)}g")
        else:
            await update.message.reply_text(
                "Métrica desconhecida. Usa: passos, sono, peso, calorias, proteina, gordura, hidratos."
            )

    async def _cmd_peso(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/peso [valor] — view or register weight."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        args = context.args or []

        if args:
            # Register weight
            try:
                weight = float(args[0].replace(",", "."))
                if not 20 < weight < 300:
                    await update.message.reply_text("Peso deve estar entre 20 e 300 kg.")
                    return
            except ValueError:
                await update.message.reply_text("Valor inválido. Uso: /peso 78.5")
                return
            today = date.today()
            self._repo.save_manual_weight(today, weight)
            await update.message.reply_text(
                f"✅ Peso registado: *{weight:.1f} kg* ({today.strftime('%d/%m/%Y')})",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # Show current weight status + last 20 records + trend chart
        yesterday = date.today() - timedelta(days=1)
        current_weight, current_date = self._repo.get_latest_weight()
        weight_stats = self._repo.get_weekly_weight_stats(yesterday)
        goals = self._repo.get_goals()
        recent_records = self._repo.get_recent_weight_records(20)
        text = format_weight_status(current_weight, current_date, weight_stats or None, goals, recent_records)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

        # Send trend chart if enough data
        trend_records = self._repo.get_weight_records_range(90)
        if len(trend_records) >= 2:
            from ..utils.charts import generate_weight_trend_chart
            weight_goal = goals.get("weight_kg") if goals else None
            chart = generate_weight_trend_chart(trend_records, weight_goal=weight_goal, days=90)
            if chart:
                await self.send_image(chart, caption="📊 Tendência de peso (90 dias)")

    async def _cmd_sync_peso(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sync_peso [dias] — sync weight from Garmin for the last N days (default 30)."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        if self._garmin_client is None:
            await update.message.reply_text("Cliente Garmin não configurado.")
            return

        args = context.args or []
        try:
            days = int(args[0]) if args else 30
            if not 1 <= days <= 365:
                await update.message.reply_text("Número de dias deve estar entre 1 e 365.")
                return
        except ValueError:
            await update.message.reply_text("Uso: /sync_peso [dias] (ex: /sync_peso 30)")
            return

        end_date = date.today() - timedelta(days=1)
        start_date = end_date - timedelta(days=days - 1)
        await update.message.reply_text(
            f"⏳ A sincronizar peso de {start_date.strftime('%d/%m/%Y')} a {end_date.strftime('%d/%m/%Y')}..."
        )

        found = 0
        for i in range(days):
            target = start_date + timedelta(days=i)
            weight = self._garmin_client.get_weight_data(target)
            if weight is not None:
                self._repo.save_manual_weight(target, weight)
                found += 1
            await asyncio.sleep(0.3)

        await update.message.reply_text(
            f"✅ Sincronização de peso concluída!\n"
            f"• Período: {start_date.strftime('%d/%m')} – {end_date.strftime('%d/%m/%Y')}\n"
            f"• Registos encontrados: *{found}* de {days} dias",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _cmd_barriga(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/barriga [valor] — view or register waist circumference in cm."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        args = context.args or []

        if args:
            try:
                cm = float(args[0].replace(",", "."))
                if not 40 < cm < 200:
                    await update.message.reply_text("Valor deve estar entre 40 e 200 cm.")
                    return
            except ValueError:
                await update.message.reply_text("Valor inválido. Uso: /barriga 95.5")
                return
            today = date.today()
            self._repo.save_waist_entry(today, cm)
            await update.message.reply_text(
                f"✅ Barriga registada: *{cm:.1f} cm* ({today.strftime('%d/%m/%Y')})",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # Show last 10 records
        records = self._repo.get_recent_waist_records(10)
        text = format_waist_status(records)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    # ------------------------------------------------------------------ #
    # Nutrition handlers                                                    #
    # ------------------------------------------------------------------ #

    async def _cmd_comi(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """/comi [ontem|anteontem|YYYY-MM-DD] <texto|preset> — register food eaten."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return ConversationHandler.END

        args = list(context.args or [])
        try:
            target_date, args = _parse_date_prefix(args)
        except ValueError as exc:
            await update.message.reply_text(f"❌ {exc}")
            return ConversationHandler.END

        text = " ".join(args).strip()
        if not text:
            await update.message.reply_text(
                "Uso: /comi 2 ovos e 1 torrada\n"
                "      /comi ontem 2 ovos e 1 torrada\n\n"
                "Ou usa o nome de um preset guardado (ex: /comi Lanche).\n"
                "Lista de presets: /preset list"
            )
            return ConversationHandler.END

        context.user_data["pending_date"] = target_date

        # ---- Check if text matches a saved meal preset (case-insensitive) ----
        preset = self._repo.get_meal_preset_by_name(text)
        if preset is not None:
            if not preset.items:
                await update.message.reply_text(f"❌ O preset \"{preset.name}\" está vazio.")
                return ConversationHandler.END
            context.user_data["pending_preset"] = preset
            msg = format_meal_preset_confirmation(preset.name, preset.items)
            if target_date != date.today():
                msg += f"\n\n📅 A registar em: *{target_date.strftime('%d/%m/%Y')}*"
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Confirmar", callback_data="preset_confirm"),
                    InlineKeyboardButton("❌ Cancelar", callback_data="food_cancel"),
                ]
            ])
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
            return _AWAITING_CONFIRMATION

        # ---- Check food cache before calling the LLM ----
        normalized_query = text.lower().strip()
        cached = self._repo.get_food_cache(normalized_query)
        if cached is not None:
            from ..nutrition.service import FoodItemResult
            items = [FoodItemResult(**d) for d in cached]
            context.user_data["pending_food"] = items
            # pending_cache_query intentionally NOT set — no need to re-cache a hit
            msg = format_food_confirmation(items)
            msg += "\n\n⚡ _Valores em cache_"
            if target_date != date.today():
                msg += f"\n\n📅 A registar em: *{target_date.strftime('%d/%m/%Y')}*"
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Confirmar", callback_data="food_confirm"),
                    InlineKeyboardButton("❌ Cancelar", callback_data="food_cancel"),
                ]
            ])
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
            return _AWAITING_CONFIRMATION

        # ---- Fall through to AI text parsing ----
        if self._nutrition_service is None:
            await update.message.reply_text(
                "⚠️ Funcionalidade de nutrição não configurada. Adiciona GROQ_API_KEY ao ficheiro .env."
            )
            return ConversationHandler.END

        await update.message.reply_text("⏳ A processar...")
        try:
            items = self._nutrition_service.process_text(text)
        except Exception as exc:
            logger.error("Nutrition process_text failed: %s", exc, exc_info=True)
            await update.message.reply_text("❌ Erro ao processar o texto. Tenta novamente.")
            return ConversationHandler.END

        if not items:
            await update.message.reply_text("Não consegui identificar alimentos no texto. Tenta ser mais específico.")
            return ConversationHandler.END

        context.user_data["pending_food"] = items
        context.user_data["pending_cache_query"] = normalized_query  # save to cache on confirm
        msg = format_food_confirmation(items)
        if target_date != date.today():
            msg += f"\n\n📅 A registar em: *{target_date.strftime('%d/%m/%Y')}*"
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Confirmar", callback_data="food_confirm"),
                InlineKeyboardButton("❌ Cancelar", callback_data="food_cancel"),
            ]
        ])
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
        return _AWAITING_CONFIRMATION

    async def _confirm_food(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Callback: user confirmed food entry."""
        query = update.callback_query
        await query.answer()
        items = context.user_data.pop("pending_food", [])
        if not items:
            await query.edit_message_text("❌ Sessão expirada. Tenta /comi novamente.")
            return ConversationHandler.END

        target_date = context.user_data.pop("pending_date", date.today())
        entries = [
            {
                "name": item.name,
                "quantity": item.quantity,
                "unit": item.unit,
                "calories": item.calories,
                "protein_g": item.protein_g,
                "fat_g": item.fat_g,
                "carbs_g": item.carbs_g,
                "fiber_g": item.fiber_g,
                "source": item.source,
                "barcode": item.barcode,
            }
            for item in items
        ]
        self._repo.save_food_entries(target_date, entries)
        total_cal = sum(item.calories or 0 for item in items)

        # Persist to food cache so the same query skips the LLM next time
        pending_query = context.user_data.pop("pending_cache_query", None)
        if pending_query:
            import dataclasses
            self._repo.set_food_cache(pending_query, [dataclasses.asdict(item) for item in items])

        date_label = f" ({target_date.strftime('%d/%m/%Y')})" if target_date != date.today() else ""
        msg = f"✅ Registado{date_label}! Total: {int(total_cal)} kcal"
        from .formatters import format_remaining_macros
        goals = self._repo.get_goals()
        totals = self._repo.get_daily_nutrition(target_date)
        garmin_data = None
        if self._garmin_client:
            try:
                garmin_data = self._garmin_client.get_activity_data(target_date)
            except Exception:
                pass
        remaining = format_remaining_macros(totals, goals, garmin_data)
        if remaining:
            msg += f"\n\n{remaining}"

        await query.edit_message_text(msg)
        return ConversationHandler.END

    async def _confirm_preset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Callback: user confirmed a meal preset — save items to food_entries."""
        query = update.callback_query
        await query.answer()
        preset = context.user_data.pop("pending_preset", None)
        if preset is None:
            await query.edit_message_text("❌ Sessão expirada. Tenta /comi novamente.")
            return ConversationHandler.END

        target_date = context.user_data.pop("pending_date", date.today())
        entries = [
            {
                "name": item.name,
                "quantity": item.quantity,
                "unit": item.unit,
                "calories": item.calories,
                "protein_g": item.protein_g,
                "fat_g": item.fat_g,
                "carbs_g": item.carbs_g,
                "fiber_g": item.fiber_g,
                "source": "meal_preset",
            }
            for item in preset.items
        ]
        self._repo.save_food_entries(target_date, entries)
        total_cal = sum(item.calories or 0 for item in preset.items)

        date_label = f" ({target_date.strftime('%d/%m/%Y')})" if target_date != date.today() else ""
        msg = f"✅ Preset \"{preset.name}\" registado{date_label}! Total: {int(total_cal)} kcal"
        from .formatters import format_remaining_macros
        goals = self._repo.get_goals()
        totals = self._repo.get_daily_nutrition(target_date)
        garmin_data = None
        if self._garmin_client:
            try:
                garmin_data = self._garmin_client.get_activity_data(target_date)
            except Exception:
                pass
        remaining = format_remaining_macros(totals, goals, garmin_data)
        if remaining:
            msg += f"\n\n{remaining}"

        await query.edit_message_text(msg)
        return ConversationHandler.END

    async def _cancel_food(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Callback or /cancelar: user cancelled food entry or preset creation."""
        context.user_data.pop("pending_food", None)
        context.user_data.pop("pending_barcode_item", None)
        context.user_data.pop("pending_preset", None)
        context.user_data.pop("pending_preset_name", None)
        context.user_data.pop("pending_preset_items", None)
        context.user_data.pop("pending_date", None)
        context.user_data.pop("pending_cache_query", None)
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text("❌ Registo cancelado.")
        else:
            await update.message.reply_text("❌ Registo cancelado.")
        return ConversationHandler.END

    async def _cmd_preset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """/preset <create|list|delete> [nome] — manage meal presets."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return ConversationHandler.END

        args = context.args or []
        subcommand = args[0].lower() if args else ""

        if subcommand == "list":
            presets = self._repo.list_meal_presets()
            text = format_meal_presets_list(presets)
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
            return ConversationHandler.END

        if subcommand == "delete":
            if len(args) < 2:
                await update.message.reply_text("Uso: /preset delete <nome>")
                return ConversationHandler.END
            name = " ".join(args[1:])
            deleted = self._repo.delete_meal_preset(name)
            if deleted:
                await update.message.reply_text(f"🗑 Preset \"{name}\" apagado.")
            else:
                await update.message.reply_text(f"❌ Preset \"{name}\" não encontrado.")
            return ConversationHandler.END

        if subcommand == "create":
            if len(args) < 2:
                await update.message.reply_text("Uso: /preset create <nome>\n\nExemplo: /preset create Lanche")
                return ConversationHandler.END
            preset_name = " ".join(args[1:])
            context.user_data["pending_preset_name"] = preset_name
            context.user_data["pending_preset_items"] = []

            await update.message.reply_text(
                f"💾 *A criar preset \"{preset_name}\"*\n\n"
                "Envia cada item numa linha separada, no formato:\n"
                "`<qtd> <nome>: <cal>cal <P>p <G>g <HC>hc <F>f`\n\n"
                "Exemplos:\n"
                "`1 Pudim Continente +Proteína: 148cal 19p 3g 10hc 1f`\n"
                "`2 Mini Babybell Light: 100cal 12p 6g 0hc 0f`\n\n"
                "Quando terminares, clica em *Concluído* ou envia /done.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return _AWAITING_PRESET_ITEMS

        # Unknown subcommand
        await update.message.reply_text(
            "Comandos de preset:\n"
            "• /preset create <nome> — criar preset (interativo)\n"
            "• /preset list — listar presets\n"
            "• /preset delete <nome> — apagar preset"
        )
        return ConversationHandler.END

    async def _handle_preset_item(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """MessageHandler: user is adding items to a preset interactively."""
        text = (update.message.text or "").strip()
        preset_name = context.user_data.get("pending_preset_name", "")
        items: list[dict] = context.user_data.get("pending_preset_items", [])

        parsed = parse_preset_item_line(text)
        if parsed is None:
            await update.message.reply_text(
                "❌ Formato inválido. Usa:\n"
                "`<qtd> <nome>: <cal>cal <P>p <G>g <HC>hc <F>f`\n\n"
                "Exemplo: `1 Pudim Proteína: 148cal 19p 3g 10hc 1f`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return _AWAITING_PRESET_ITEMS

        items.append(parsed)
        context.user_data["pending_preset_items"] = items

        cal = int(parsed["calories"] or 0)
        prot = int(parsed.get("protein_g") or 0)
        fat = int(parsed.get("fat_g") or 0)
        carbs = int(parsed.get("carbs_g") or 0)
        fiber = int(parsed.get("fiber_g") or 0)
        qty = parsed["quantity"]
        qty_str = f"{int(qty)}" if qty == int(qty) else f"{qty}"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Concluído", callback_data="preset_save"),
                InlineKeyboardButton("❌ Cancelar", callback_data="food_cancel"),
            ]
        ])
        await update.message.reply_text(
            f"✅ *{qty_str}× {parsed['name'].title()}* adicionado\n"
            f"   {cal} kcal | P: {prot}g | G: {fat}g | HC: {carbs}g | F: {fiber}g\n\n"
            f"_Total de itens: {len(items)}_\n\n"
            "Envia mais um item ou clica *Concluído*.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        return _AWAITING_PRESET_ITEMS

    async def _cmd_preset_done(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """/done — finish adding items to a preset."""
        return await self._finish_preset(update.message.reply_text, context)

    async def _save_preset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Callback: 'Concluído' button pressed — save preset."""
        query = update.callback_query
        await query.answer()
        return await self._finish_preset(query.edit_message_text, context)

    async def _finish_preset(self, reply_fn, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Shared logic: validate and save the preset being built."""
        preset_name = context.user_data.pop("pending_preset_name", None)
        items: list[dict] = context.user_data.pop("pending_preset_items", [])

        if not preset_name:
            await reply_fn("❌ Sessão expirada. Tenta /preset create novamente.")
            return ConversationHandler.END

        if not items:
            await reply_fn("❌ Não adicionaste nenhum item. Preset não guardado.")
            return ConversationHandler.END

        self._repo.save_meal_preset(preset_name, items)
        total_cal = sum(i.get("calories") or 0 for i in items)
        item_count = len(items)
        await reply_fn(
            f"✅ Preset *\"{preset_name}\"* guardado com {item_count} item(s) ({int(total_cal)} kcal).\n\n"
            f"_Usa /comi {preset_name} para registar._",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    async def _handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """MessageHandler: user sent a photo (barcode scan)."""
        if not self._auth_check(update):
            return ConversationHandler.END
        if self._nutrition_service is None:
            return ConversationHandler.END  # silently ignore — nutrition not configured

        await update.message.reply_text("📷 A ler código de barras...")
        photo = update.message.photo[-1]  # largest size
        file = await photo.get_file()
        image_bytes = await file.download_as_bytearray()

        try:
            result = self._nutrition_service.process_barcode(bytes(image_bytes))
        except Exception as exc:
            logger.error("Barcode processing failed: %s", exc, exc_info=True)
            await update.message.reply_text("❌ Erro ao processar a imagem.")
            return ConversationHandler.END

        if result is None:
            await update.message.reply_text(
                "❌ Não consegui ler o código de barras. Tenta com melhor iluminação ou usa /comi."
            )
            return ConversationHandler.END

        context.user_data["pending_barcode_item"] = result
        await update.message.reply_text(
            f"Encontrei: *{result.name.title()}*\nQuantas unidades comeste?",
            parse_mode=ParseMode.MARKDOWN,
        )
        return _AWAITING_BARCODE_QUANTITY

    async def _handle_barcode_quantity(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """User replied with quantity for barcode product."""
        text = (update.message.text or "").strip()
        try:
            qty = float(text.replace(",", "."))
            if qty <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Por favor responde com um número (ex: 1, 2, 0.5).")
            return _AWAITING_BARCODE_QUANTITY

        item = context.user_data.get("pending_barcode_item")
        if not item:
            await update.message.reply_text("❌ Sessão expirada. Tenta enviar a foto novamente.")
            return ConversationHandler.END

        # Recalculate nutrients for the new quantity
        from ..nutrition.openfoodfacts import NutritionData
        from ..nutrition.service import NutritionService
        nutrition = NutritionData(
            product_name=item.name,
            calories_per_100g=(item.calories / (item.quantity / 100 * (item.quantity or 100))) if item.calories else None,
            protein_per_100g=None,
            fat_per_100g=None,
            carbs_per_100g=None,
            fiber_per_100g=None,
            serving_size_g=None,
        )
        # Simpler: just scale linearly by new qty vs original
        scale = qty / item.quantity if item.quantity else 1.0
        from dataclasses import replace as dc_replace
        scaled_item = dc_replace(
            item,
            quantity=qty,
            calories=round(item.calories * scale, 1) if item.calories else None,
            protein_g=round(item.protein_g * scale, 1) if item.protein_g else None,
            fat_g=round(item.fat_g * scale, 1) if item.fat_g else None,
            carbs_g=round(item.carbs_g * scale, 1) if item.carbs_g else None,
            fiber_g=round(item.fiber_g * scale, 1) if item.fiber_g else None,
        )
        context.user_data["pending_food"] = [scaled_item]
        context.user_data.pop("pending_barcode_item", None)

        msg = format_food_confirmation([scaled_item])
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Confirmar", callback_data="food_confirm"),
                InlineKeyboardButton("❌ Cancelar", callback_data="food_cancel"),
            ]
        ])
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
        return _AWAITING_CONFIRMATION

    async def _cmd_nutricao(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/nutricao (alias /dieta) — daily nutrition summary."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        today = date.today()
        entries = self._repo.get_food_entries(today)
        totals = self._repo.get_daily_nutrition(today)
        # Fetch today's calories in real-time from Garmin API
        garmin_data = None
        if self._garmin_client:
            try:
                activity = self._garmin_client.get_activity_data(today)
                garmin_data = activity
            except Exception as exc:
                logger.warning("Failed to fetch today's Garmin data: %s", exc)
        text = format_nutrition_day(entries, totals, garmin_data)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_apagar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/apagar — delete last food entry today."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        deleted = self._repo.delete_last_food_entry(date.today())
        if deleted:
            cal = int(deleted.calories) if deleted.calories else "?"
            qty_str = f"{int(deleted.quantity)}" if deleted.unit == "un" else f"{deleted.quantity:g}{deleted.unit}"
            await update.message.reply_text(
                f"🗑 Apagada última entrada: *{deleted.name.title()} ({qty_str}) — {cal} kcal*",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text("Não há entradas para apagar hoje.")

    # ------------------------------------------------------------------ #
    # Training handlers                                                    #
    # ------------------------------------------------------------------ #

    async def _cmd_equipamento(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/equipamento [minutos N | <texto>] — ver ou configurar equipamento de ginásio."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return

        args = context.args or []

        # No args: show current settings
        if not args:
            equipment = self._repo.get_setting("gym_equipment")
            minutes = self._repo.get_setting("gym_training_minutes") or "45"
            if equipment:
                await update.message.reply_text(
                    f"🏋️ *Equipamento configurado:*\n{equipment}\n\n"
                    f"⏱ Tempo de treino: {minutes} min\n\n"
                    f"Para alterar: `/equipamento <lista de equipamento>`\n"
                    f"Para alterar tempo: `/equipamento minutos 60`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await update.message.reply_text(
                    "Equipamento não configurado.\n"
                    "Usa `/equipamento <lista>` para configurar.\n\n"
                    "Exemplo: `/equipamento halteres 2\\-20kg, barra de dominadas, banco`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            return

        # /equipamento minutos N  (also accepts "tempo" as alias)
        if args[0].lower() in ("minutos", "tempo"):
            if len(args) < 2 or not args[1].isdigit():
                await update.message.reply_text(
                    "Usa: `/equipamento minutos <número>`", parse_mode=ParseMode.MARKDOWN
                )
                return
            minutes = int(args[1])
            if not 10 <= minutes <= 180:
                await update.message.reply_text("O tempo deve ser entre 10 e 180 minutos.")
                return
            self._repo.set_setting("gym_training_minutes", str(minutes))
            await update.message.reply_text(f"✅ Tempo de treino atualizado para {minutes} minutos.")
            return

        # Update equipment
        equipment = " ".join(args)
        self._repo.set_setting("gym_equipment", equipment)
        await update.message.reply_text(
            f"✅ Equipamento guardado:\n{equipment}\n\n"
            "Usa `/sync_treino` para gerar o treino de hoje.",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _cmd_treinei(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/treinei [data] <descrição> — registar o treino feito num dado dia."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return

        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Descreve o treino que fizeste.\n"
                "Exemplo: `/treinei Bench press 4x8, Pull\\-ups 3x10`\n"
                "Para registar noutro dia: `/treinei ontem Squat 4x10`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        try:
            target_date, remaining_args = _parse_date_prefix(args)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return

        if not remaining_args:
            await update.message.reply_text("Adiciona a descrição do treino após a data.")
            return

        description = " ".join(remaining_args)
        self._repo.upsert_training_entry(target_date, description)
        label = "hoje" if target_date == date.today() else target_date.strftime("%d/%m/%Y")
        await update.message.reply_text(f"✅ Treino registado ({label}):\n{description}")

    async def _cmd_sync_treino(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sync_treino — sync, send yesterday's summary, and generate a workout."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        if self._garmin_sync is None:
            await update.message.reply_text("Sync não configurado.")
            return
        if not self._config.groq_api_key:
            await update.message.reply_text(
                "Groq não configurado. Define `GROQ_API_KEY` no `.env`.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # 1. Sync Garmin
        await update.message.reply_text("⏳ A sincronizar com o Garmin Connect...")
        try:
            self._garmin_sync()
        except Exception as exc:
            logger.error("Manual sync failed: %s", exc)
            await update.message.reply_text(
                format_error_message("sync manual", exc), parse_mode=ParseMode.MARKDOWN
            )
            return

        # 2. Send daily summary (same as /sync)
        await self._send_yesterday_report()

        # 3. Check equipment
        equipment = self._repo.get_setting("gym_equipment")
        if not equipment:
            await update.message.reply_text(
                "⚠️ Equipamento não configurado.\n"
                "Usa `/equipamento <lista>` para configurar.\n\n"
                "Exemplo: `/equipamento halteres 2\\-20kg, barra de dominadas, banco`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        training_minutes = int(self._repo.get_setting("gym_training_minutes") or "45")

        # 4. Get training history — combined manual + Garmin (last 7 days)
        training_history = self._repo.get_training_summary_for_llm(days=7)

        # 5. Build yesterday's metrics for the workout generator
        yesterday = date.today() - timedelta(days=1)
        row = self._repo.get_metrics_by_date(yesterday)
        if row is None:
            # Already handled by _send_yesterday_report; nothing to generate
            return

        metrics = _row_to_metrics(row)
        nutrition_totals = self._repo.get_daily_nutrition(yesterday)
        nutrition = nutrition_totals if nutrition_totals.get("entry_count", 0) > 0 else None

        # 6. Generate and send workout
        await update.message.reply_text("💪 A gerar sugestão de treino...")
        weight_history = self._repo.get_recent_weight_records(10)
        waist_history = self._repo.get_recent_waist_records(10)
        goals = self._repo.get_goals()
        weight_goal = goals.get("weight_kg")
        from ..training.recommender import generate_workout
        workout_text = generate_workout(
            metrics=metrics,
            nutrition=nutrition,
            equipment=equipment,
            training_minutes=training_minutes,
            training_history=training_history,
            api_key=self._config.groq_api_key,
            weight_history=weight_history or None,
            waist_history=waist_history or None,
            weight_goal=weight_goal,
        )

        if workout_text:
            await self._send(format_workout_section(workout_text))
        else:
            await update.message.reply_text(
                "⚠️ Não foi possível gerar a sugestão de treino. Tenta novamente mais tarde."
            )

    async def _cmd_sync_atividades(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sync_atividades [hoje] — fetch Garmin activities and save to training log."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        if self._garmin_client is None:
            await update.message.reply_text("Cliente Garmin não configurado.")
            return

        args = context.args or []
        if args and args[0].lower() == "hoje":
            target_day = date.today()
            day_label = f"{target_day.strftime('%d/%m/%Y')} (hoje)"
        else:
            target_day = date.today() - timedelta(days=1)
            day_label = f"{target_day.strftime('%d/%m/%Y')} (ontem)"

        await update.message.reply_text(f"⏳ A buscar atividades de {day_label}...")

        activities = self._garmin_client.get_activities_for_date(target_day)

        if activities:
            for act in activities:
                self._repo.upsert_garmin_activity(
                    activity_id=act["activity_id"],
                    day=target_day,
                    name=act["name"],
                    type_key=act.get("type_key"),
                    duration_min=act.get("duration_min"),
                    calories=act.get("calories"),
                    distance_km=act.get("distance_km"),
                )

        text = format_activity_sync(activities, day_label)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    # ------------------------------------------------------------------ #
    # Application lifecycle                                                #
    # ------------------------------------------------------------------ #

    def build_application(self) -> Application:
        """Build and configure the telegram Application with all command handlers."""
        app = Application.builder().token(self._config.telegram_bot_token).build()

        app.add_handler(CommandHandler("hoje", self._cmd_hoje))
        app.add_handler(CommandHandler("ontem", self._cmd_ontem))
        app.add_handler(CommandHandler("semana", self._cmd_semana))
        app.add_handler(CommandHandler("mes", self._cmd_mes))
        app.add_handler(CommandHandler("sync", self._cmd_sync))
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("ajuda", self._cmd_ajuda))
        app.add_handler(CommandHandler("help", self._cmd_ajuda))
        app.add_handler(CommandHandler("historico", self._cmd_historico))
        app.add_handler(CommandHandler("exportar", self._cmd_exportar))
        app.add_handler(CommandHandler("backfill", self._cmd_backfill))
        app.add_handler(CommandHandler("objetivo", self._cmd_objetivo))
        app.add_handler(CommandHandler("peso", self._cmd_peso))
        app.add_handler(CommandHandler("sync_peso", self._cmd_sync_peso))
        app.add_handler(CommandHandler("barriga", self._cmd_barriga))
        app.add_handler(CommandHandler("nutricao", self._cmd_nutricao))
        app.add_handler(CommandHandler("dieta", self._cmd_nutricao))
        app.add_handler(CommandHandler("apagar", self._cmd_apagar))
        app.add_handler(CommandHandler("sync_treino", self._cmd_sync_treino))
        app.add_handler(CommandHandler("equipamento", self._cmd_equipamento))
        app.add_handler(CommandHandler("treinei", self._cmd_treinei))
        app.add_handler(CommandHandler("sync_atividades", self._cmd_sync_atividades))

        # Nutrition conversation (text entry + barcode + meal presets)
        conv = ConversationHandler(
            entry_points=[
                CommandHandler("comi", self._cmd_comi),
                CommandHandler("preset", self._cmd_preset),
                MessageHandler(filters.PHOTO, self._handle_photo),
            ],
            states={
                _AWAITING_CONFIRMATION: [
                    CallbackQueryHandler(self._confirm_food, pattern="^food_confirm$"),
                    CallbackQueryHandler(self._confirm_preset, pattern="^preset_confirm$"),
                    CallbackQueryHandler(self._cancel_food, pattern="^food_cancel$"),
                ],
                _AWAITING_BARCODE_QUANTITY: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_barcode_quantity),
                ],
                _AWAITING_PRESET_ITEMS: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_preset_item),
                    CommandHandler("done", self._cmd_preset_done),
                    CallbackQueryHandler(self._save_preset, pattern="^preset_save$"),
                    CallbackQueryHandler(self._cancel_food, pattern="^food_cancel$"),
                ],
            },
            fallbacks=[CommandHandler("cancelar", self._cancel_food)],
            conversation_timeout=600,
        )
        app.add_handler(conv)

        self._app = app
        return app

    async def register_commands(self) -> None:
        """Register command list with BotFather so they appear in the Telegram UI."""
        bot = Bot(token=self._config.telegram_bot_token)
        commands = [
            BotCommand("hoje", "Ponto de situação do dia atual (ao vivo)"),
            BotCommand("ontem", "Resumo de ontem"),
            BotCommand("semana", "Relatório semanal"),
            BotCommand("mes", "Relatório mensal"),
            BotCommand("sync", "Sincronizar e ver resumo do dia anterior"),
            BotCommand("backfill", "Sincronizar dias em falta"),
            BotCommand("historico", "Ver dia específico ou últimos N dias"),
            BotCommand("exportar", "Exportar dados em CSV"),
            BotCommand("objetivo", "Ver ou definir objetivos"),
            BotCommand("peso", "Ver ou registar peso (ex: /peso 78.5)"),
            BotCommand("sync_peso", "Sincronizar peso do Garmin (ex: /sync_peso 30)"),
            BotCommand("barriga", "Ver ou registar perímetro abdominal (ex: /barriga 95.5)"),
            BotCommand("status", "Estado do bot"),
            BotCommand("ajuda", "Lista de comandos"),
            BotCommand("comi", "Registar alimento ou preset (ex: /comi Lanche)"),
            BotCommand("nutricao", "Resumo nutricional do dia"),
            BotCommand("apagar", "Apagar último alimento registado"),
            BotCommand("preset", "Gerir presets de refeição (create/list/delete)"),
            BotCommand("sync_treino", "Sincronizar e gerar sugestão de treino"),
            BotCommand("sync_atividades", "Importar atividades do Garmin (ex: /sync_atividades hoje)"),
            BotCommand("equipamento", "Ver ou configurar equipamento de ginásio"),
            BotCommand("treinei", "Registar treino feito (ex: /treinei Bench 4x8)"),
        ]
        await bot.set_my_commands(commands)
        logger.info("Telegram commands registered with BotFather")

    async def send_image(self, image_bytes: bytes, caption: str | None = None) -> None:
        """Send a photo (e.g. chart) to the configured chat.

        Args:
            image_bytes: Raw PNG/JPEG bytes.
            caption: Optional caption for the image.
        """
        bot = Bot(token=self._config.telegram_bot_token)
        await bot.send_photo(
            chat_id=self._chat_id,
            photo=image_bytes,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN,
        )
