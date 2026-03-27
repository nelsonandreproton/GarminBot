"""System command handlers: /sync, /status, /ajuda, /exportar, /backfill."""

from __future__ import annotations

import csv
import io as _io
import logging
from datetime import date, timedelta

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..helpers import _is_rate_limited, safe_command

logger = logging.getLogger(__name__)


class SystemMixin:
    """Mixin providing system/admin command handlers."""

    @safe_command
    async def _cmd_sync(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sync — sync yesterday's Garmin data, check for new newsletter post, send daily summary."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        if self._garmin_sync is None:
            await update.message.reply_text("Sync não configurado.")
            return
        await update.message.reply_text("⏳ A sincronizar com o Garmin Connect...")
        from ..formatters import format_error_message
        try:
            self._garmin_sync()
        except Exception as exc:
            logger.error("Manual sync failed: %s", exc)
            await update.message.reply_text(format_error_message("sync manual", exc), parse_mode=ParseMode.MARKDOWN)
            return
        # Check for new newsletter post (runs synchronously before report is sent)
        if self._newsletter_check is not None:
            try:
                self._newsletter_check()
            except Exception as exc:
                logger.warning("Newsletter check failed during /sync: %s", exc)
        # Send yesterday's report directly (already in async context — no _run_async needed)
        await self._send_yesterday_report()

    @safe_command
    async def _cmd_pump(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/pump — scrape all historical Pump newsletter posts and send a personalised insights document."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        if self._newsletter_bulk is None:
            await update.message.reply_text("Newsletter não configurado (GROQ_API_KEY em falta?).")
            return
        await update.message.reply_text(
            "⏳ A fazer scraping de todos os artigos do The Pump e a analisar com IA...\n"
            "Isto pode demorar alguns minutos."
        )
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._newsletter_bulk)
            # Fetch the historical insight that was just generated and stored in DB
            insight = self._repo.get_latest_historical_insight()
            if insight:
                import io as _io
                from telegram import Bot, InputFile
                tg = Bot(token=self._config.telegram_bot_token)
                doc_bytes = insight.insight_pt.encode("utf-8")
                await tg.send_document(
                    chat_id=self._chat_id,
                    document=InputFile(_io.BytesIO(doc_bytes), filename="the_pump_insights_historicos.md"),
                    caption=f"📚 *The Pump — Insights Históricos*\nConsulta este documento como referência personalizada.",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text("⚠️ Scraping concluído mas não foi possível gerar o documento.")
        except Exception as exc:
            logger.error("/pump bulk scrape failed: %s", exc, exc_info=True)
            await update.message.reply_text("❌ Falhou o scraping do The Pump. Verifica os logs.")

    @safe_command
    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/status — bot status and last sync info."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        from ..formatters import format_status
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

    @safe_command
    async def _cmd_ajuda(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/ajuda — list all commands."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        from ..formatters import format_help_message
        await update.message.reply_text(format_help_message())

    @safe_command
    async def _cmd_exportar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/exportar [N|nutricao] — export Garmin or nutrition data as CSV."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        from telegram import Bot, InputFile
        args = context.args or []

        # /exportar nutricao
        if args and args[0].lower() == "nutricao":
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

    @safe_command
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
        try:
            self._garmin_backfill(missing)
        except Exception as exc:
            logger.error("Backfill failed: %s", exc, exc_info=True)
            await update.message.reply_text(f"❌ Backfill falhou: {exc}")
            return
        await update.message.reply_text(f"✅ Backfill concluído para {len(missing)} dias.")
