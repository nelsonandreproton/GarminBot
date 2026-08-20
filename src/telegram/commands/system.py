"""System command handlers: /sync, /status, /ajuda, /exportar, /backfill."""

from __future__ import annotations

import csv
import io as _io
import logging
from datetime import date, timedelta

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..helpers import _is_rate_limited, _parse_export_args, safe_command

logger = logging.getLogger(__name__)


class SystemMixin:
    """Mixin providing system/admin command handlers."""

    @safe_command
    async def _cmd_sync(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sync — sync yesterday's Garmin data and send daily summary."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        if self._garmin_sync is None:
            await update.message.reply_text("Sync não configurado.")
            return
        await update.message.reply_text("⏳ A sincronizar com o Garmin Connect...")
        from ..formatters import format_error_message
        try:
            result = self._garmin_sync()
        except Exception as exc:
            logger.error("Manual sync failed: %s", exc)
            await update.message.reply_text(format_error_message("sync manual", exc), parse_mode=ParseMode.MARKDOWN)
            return
        for warning in (result or {}).get("warnings", []):
            await update.message.reply_text(f"⚠️ {warning}")
        await self._send_yesterday_report()

    @safe_command
    async def _cmd_pump(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/pump — fetch today's The Pump post and send personalised insights."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        if self._newsletter_check is None:
            await update.message.reply_text("Newsletter não configurado (GROQ_API_KEY em falta?).")
            return
        await update.message.reply_text("⏳ A verificar The Pump newsletter...")
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._newsletter_check)
        except Exception as exc:
            logger.error("/pump failed: %s", exc, exc_info=True)
            await update.message.reply_text(f"❌ Erro ao verificar The Pump: {exc}")
            return
        insight = self._repo.get_latest_daily_insight()
        if insight:
            self._repo.mark_insight_sent(insight.id)
            await self._send(insight.insight_pt)
        else:
            await update.message.reply_text("📰 Não foi possível obter o artigo do The Pump.")

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
        """/exportar [N|refeicoes|treinos [formato] [inicio] [fim]] — export Garmin metrics, meals, or activities."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        from telegram import Bot, InputFile
        args = context.args or []

        # /exportar refeicoes (alias: nutricao) [csv|json|xlsx] [YYYY-MM-DD] [YYYY-MM-DD]
        if args and args[0].lower() in ("refeicoes", "nutricao"):
            try:
                fmt, start, end = _parse_export_args(args[1:])
            except ValueError as exc:
                await update.message.reply_text(f"❌ {exc}")
                return
            if fmt == "xlsx":
                try:
                    import openpyxl  # noqa: F401
                except ImportError:
                    await update.message.reply_text(
                        "⚠️ Exportação em XLSX não disponível (openpyxl não instalado)."
                    )
                    return

            await self._sync_missing_fatsecret_days(start, end, update)

            entries = self._repo.get_food_entries_range(start, end)
            if not entries:
                await update.message.reply_text("Sem dados de refeições para exportar nesse período.")
                return

            from ..meal_export import build_export
            file_bytes = build_export(fmt, entries)
            filename = f"refeicoes_export_{start}_{end}.{fmt}"
            bot = Bot(token=self._config.telegram_bot_token)
            await bot.send_document(
                chat_id=self._chat_id,
                document=InputFile(_io.BytesIO(file_bytes), filename=filename),
                caption=f"🥗 {len(entries)} refeições exportadas ({start.strftime('%d/%m/%Y')} a {end.strftime('%d/%m/%Y')})",
            )
            return

        # /exportar treinos [csv|json|xlsx] [YYYY-MM-DD] [YYYY-MM-DD]
        if args and args[0].lower() == "treinos":
            try:
                fmt, start, end = _parse_export_args(args[1:])
            except ValueError as exc:
                await update.message.reply_text(f"❌ {exc}")
                return
            if fmt == "xlsx":
                try:
                    import openpyxl  # noqa: F401
                except ImportError:
                    await update.message.reply_text(
                        "⚠️ Exportação em XLSX não disponível (openpyxl não instalado)."
                    )
                    return

            entries = self._repo.get_garmin_activities_range(start, end)
            if not entries:
                await update.message.reply_text("Sem dados de treinos para exportar nesse período.")
                return

            from ..activity_export import build_export
            file_bytes = build_export(fmt, entries)
            filename = f"treinos_export_{start}_{end}.{fmt}"
            bot = Bot(token=self._config.telegram_bot_token)
            await bot.send_document(
                chat_id=self._chat_id,
                document=InputFile(_io.BytesIO(file_bytes), filename=filename),
                caption=f"🏋️ {len(entries)} treinos exportados ({start.strftime('%d/%m/%Y')} a {end.strftime('%d/%m/%Y')})",
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

    async def _sync_missing_fatsecret_days(self, start: date, end: date, update: Update) -> None:
        """Fetch and store FatSecret meals for any day in [start, end] not yet in food_entries.

        Best-effort: a failure on one day is logged and skipped, never raised —
        the export must still proceed with whatever is already in the local table.
        """
        if self._fatsecret_client is None:
            return
        missing = self._repo.get_food_entry_missing_dates(start, end)
        if not missing:
            return

        from ...nutrition.fatsecret_client import _redact
        from ...nutrition.fatsecret_mapper import map_fatsecret_entries

        await update.message.reply_text(f"⏳ A sincronizar {len(missing)} dia(s) com o FatSecret...")
        synced = 0
        for day in missing:
            try:
                raw = self._fatsecret_client.get_food_entries(day)
                mapped = map_fatsecret_entries(raw)
                if mapped:
                    self._repo.upsert_fatsecret_entries(day, mapped)
                    synced += 1
            except Exception as exc:
                logger.warning("FatSecret sync failed for %s during export (skipping): %s", day, _redact(exc))
        if synced:
            await update.message.reply_text(f"✅ {synced} dia(s) sincronizado(s) com o FatSecret.")

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
