"""Telegram bot: message sending and command handlers."""

from __future__ import annotations

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
    format_daily_summary,
    format_error_message,
    format_food_confirmation,
    format_meal_preset_confirmation,
    format_meal_presets_list,
    format_monthly_report,
    format_nutrition_day,
    format_status,
    format_weekly_report,
    format_weight_status,
)

# ConversationHandler states
_AWAITING_CONFIRMATION = 0
_AWAITING_BARCODE_QUANTITY = 1
_AWAITING_PRESET_SAVE = 2

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

    async def send_weekly_report(self, stats: dict[str, Any], weight_stats: dict[str, Any] | None = None) -> None:
        """Send the weekly report message with week-over-week comparison, weight, and nutrition."""
        end_date = stats.get("end_date")
        prev_stats = self._repo.get_previous_weekly_stats(end_date) if end_date else None
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
        """/ontem — yesterday's full summary."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        yesterday = date.today() - timedelta(days=1)
        row = self._repo.get_metrics_by_date(yesterday)
        if row is None:
            await update.message.reply_text("Sem dados para ontem. Tenta /sync primeiro.")
            return
        metrics = {
            "date": row.date,
            "sleep_hours": row.sleep_hours,
            "sleep_score": row.sleep_score,
            "sleep_quality": row.sleep_quality,
            "steps": row.steps,
            "active_calories": row.active_calories,
            "resting_calories": row.resting_calories,
        }
        weekly = self._repo.get_weekly_stats(yesterday)
        text = format_daily_summary(metrics, weekly_stats=weekly)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_semana(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/semana — last 7 days stats."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        stats = self._repo.get_weekly_stats(date.today() - timedelta(days=1))
        if not stats:
            await update.message.reply_text("Sem dados suficientes para a semana.")
            return
        await update.message.reply_text(format_weekly_report(stats), parse_mode=ParseMode.MARKDOWN)

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
        metrics = {
            "date": row.date,
            "sleep_hours": row.sleep_hours,
            "sleep_score": row.sleep_score,
            "sleep_quality": row.sleep_quality,
            "steps": row.steps,
            "active_calories": row.active_calories,
            "resting_calories": row.resting_calories,
            "total_calories": row.total_calories,
            "resting_heart_rate": row.resting_heart_rate,
            "avg_stress": row.avg_stress,
            "body_battery_high": row.body_battery_high,
            "body_battery_low": row.body_battery_low,
            "weight_kg": row.weight_kg,
        }
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
        metrics = {
            "date": row.date, "sleep_hours": row.sleep_hours, "sleep_score": row.sleep_score,
            "sleep_quality": row.sleep_quality, "steps": row.steps,
            "active_calories": row.active_calories, "resting_calories": row.resting_calories,
            "resting_heart_rate": row.resting_heart_rate, "avg_stress": row.avg_stress,
            "body_battery_high": row.body_battery_high, "body_battery_low": row.body_battery_low,
        }
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

        # Show current weight status
        yesterday = date.today() - timedelta(days=1)
        current_weight, current_date = self._repo.get_latest_weight()
        weight_stats = self._repo.get_weekly_weight_stats(yesterday)
        goals = self._repo.get_goals()
        text = format_weight_status(current_weight, current_date, weight_stats or None, goals)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    # ------------------------------------------------------------------ #
    # Nutrition handlers                                                    #
    # ------------------------------------------------------------------ #

    async def _cmd_comi(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """/comi <texto|preset> — register food eaten, or load a named meal preset."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return ConversationHandler.END

        text = " ".join(context.args or []).strip()
        if not text:
            await update.message.reply_text(
                "Uso: /comi 2 ovos e 1 torrada\n\nOu usa o nome de um preset guardado (ex: /comi Lanche).\n"
                "Lista de presets: /preset list"
            )
            return ConversationHandler.END

        # ---- Check if text matches a saved meal preset (case-insensitive) ----
        preset = self._repo.get_meal_preset_by_name(text)
        if preset is not None:
            if not preset.items:
                await update.message.reply_text(f"❌ O preset \"{preset.name}\" está vazio.")
                return ConversationHandler.END
            context.user_data["pending_preset"] = preset
            msg = format_meal_preset_confirmation(preset.name, preset.items)
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Confirmar", callback_data="preset_confirm"),
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
        msg = format_food_confirmation(items)
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

        today = date.today()
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
        self._repo.save_food_entries(today, entries)
        total_cal = sum(item.calories or 0 for item in items)

        msg = f"✅ Registado! Total: {int(total_cal)} kcal"
        from .formatters import format_remaining_macros
        goals = self._repo.get_goals()
        totals = self._repo.get_daily_nutrition(today)
        garmin_data = None
        if self._garmin_client:
            try:
                garmin_data = self._garmin_client.get_activity_data(today)
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

        today = date.today()
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
        self._repo.save_food_entries(today, entries)
        total_cal = sum(item.calories or 0 for item in preset.items)

        msg = f"✅ Preset \"{preset.name}\" registado! Total: {int(total_cal)} kcal"
        from .formatters import format_remaining_macros
        goals = self._repo.get_goals()
        totals = self._repo.get_daily_nutrition(today)
        garmin_data = None
        if self._garmin_client:
            try:
                garmin_data = self._garmin_client.get_activity_data(today)
            except Exception:
                pass
        remaining = format_remaining_macros(totals, goals, garmin_data)
        if remaining:
            msg += f"\n\n{remaining}"

        await query.edit_message_text(msg)
        return ConversationHandler.END

    async def _cancel_food(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Callback or /cancelar: user cancelled food entry."""
        context.user_data.pop("pending_food", None)
        context.user_data.pop("pending_barcode_item", None)
        context.user_data.pop("pending_preset", None)
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text("❌ Registo cancelado.")
        else:
            await update.message.reply_text("❌ Registo cancelado.")
        return ConversationHandler.END

    async def _cmd_preset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """/preset <create|list|delete> — manage meal presets."""
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
            if len(args) < 3:
                await update.message.reply_text(
                    "Uso: /preset create <nome> <itens>\n\n"
                    "Exemplo: /preset create Lanche 1 pudim proteína e 2 babybell light"
                )
                return ConversationHandler.END
            if self._nutrition_service is None:
                await update.message.reply_text(
                    "⚠️ Funcionalidade de nutrição não configurada. Adiciona GROQ_API_KEY ao ficheiro .env."
                )
                return ConversationHandler.END
            preset_name = args[1]
            food_text = " ".join(args[2:])
            await update.message.reply_text(f"⏳ A criar preset \"{preset_name}\"...")
            try:
                items = self._nutrition_service.process_text(food_text)
            except Exception as exc:
                logger.error("Nutrition process_text failed for preset: %s", exc, exc_info=True)
                await update.message.reply_text("❌ Erro ao processar os itens. Tenta novamente.")
                return ConversationHandler.END
            if not items:
                await update.message.reply_text("Não consegui identificar alimentos. Tenta ser mais específico.")
                return ConversationHandler.END

            # Store items + name, show confirmation before saving as preset
            context.user_data["pending_preset_name"] = preset_name
            context.user_data["pending_preset_items"] = items
            msg = format_food_confirmation(items)
            msg = f"💾 *Guardar como preset \"{preset_name}\":*\n\n" + msg.replace("📝 *Registar refeição:*\n\n", "")
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("💾 Guardar preset", callback_data="preset_save"),
                    InlineKeyboardButton("❌ Cancelar", callback_data="food_cancel"),
                ]
            ])
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
            return _AWAITING_PRESET_SAVE

        # Unknown subcommand
        await update.message.reply_text(
            "Comandos de preset:\n"
            "• /preset create <nome> <itens> — criar preset\n"
            "• /preset list — listar presets\n"
            "• /preset delete <nome> — apagar preset"
        )
        return ConversationHandler.END

    async def _save_preset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Callback: user confirmed saving items as a meal preset."""
        query = update.callback_query
        await query.answer()
        preset_name = context.user_data.pop("pending_preset_name", None)
        items = context.user_data.pop("pending_preset_items", None)
        if not preset_name or not items:
            await query.edit_message_text("❌ Sessão expirada. Tenta /preset create novamente.")
            return ConversationHandler.END

        item_dicts = [
            {
                "name": item.name,
                "quantity": item.quantity,
                "unit": item.unit,
                "calories": item.calories,
                "protein_g": item.protein_g,
                "fat_g": item.fat_g,
                "carbs_g": item.carbs_g,
                "fiber_g": item.fiber_g,
            }
            for item in items
        ]
        self._repo.save_meal_preset(preset_name, item_dicts)
        total_cal = sum(item.calories or 0 for item in items)
        item_count = len(items)
        await query.edit_message_text(
            f"✅ Preset \"{preset_name}\" guardado com {item_count} item(s) ({int(total_cal)} kcal).\n\n"
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
        app.add_handler(CommandHandler("nutricao", self._cmd_nutricao))
        app.add_handler(CommandHandler("dieta", self._cmd_nutricao))
        app.add_handler(CommandHandler("apagar", self._cmd_apagar))

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
                _AWAITING_PRESET_SAVE: [
                    CallbackQueryHandler(self._save_preset, pattern="^preset_save$"),
                    CallbackQueryHandler(self._cancel_food, pattern="^food_cancel$"),
                ],
            },
            fallbacks=[CommandHandler("cancelar", self._cancel_food)],
            conversation_timeout=300,
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
            BotCommand("status", "Estado do bot"),
            BotCommand("ajuda", "Lista de comandos"),
            BotCommand("comi", "Registar alimento ou preset (ex: /comi Lanche)"),
            BotCommand("nutricao", "Resumo nutricional do dia"),
            BotCommand("apagar", "Apagar último alimento registado"),
            BotCommand("preset", "Gerir presets de refeição (create/list/delete)"),
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
