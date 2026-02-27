"""Body metrics command handlers: /peso, /sync_peso, /barriga, /agua, /objetivo."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..helpers import _is_rate_limited, safe_command

logger = logging.getLogger(__name__)


class BodyMixin:
    """Mixin providing body metrics command handlers."""

    @safe_command
    async def _cmd_objetivo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/objetivo [passos|sono <valor>] — view or set goals."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        from ..formatters import format_goals
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

    @safe_command
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
        from ..formatters import format_weight_status
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
            from ...utils.charts import generate_weight_trend_chart
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

    @safe_command
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
        from ..formatters import format_waist_status
        records = self._repo.get_recent_waist_records(10)
        text = format_waist_status(records)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    @safe_command
    async def _cmd_agua(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/agua [ml] — register water intake or show today's total."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        args = context.args or []
        today = date.today()

        if args:
            try:
                ml = int(args[0])
                if not 1 <= ml <= 5000:
                    await update.message.reply_text("Valor deve estar entre 1 e 5000 ml.")
                    return
            except ValueError:
                await update.message.reply_text("Valor inválido. Uso: /agua 250")
                return
            self._repo.add_water_entry(today, ml)
            total = self._repo.get_daily_water(today)
            liters = total / 1000
            await update.message.reply_text(
                f"✅ *+{ml} ml* registados\\!\n💧 Total hoje: *{liters:.1f} L* \\({total} ml\\)",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        # Show today's total
        total = self._repo.get_daily_water(today)
        if total == 0:
            await update.message.reply_text(
                "💧 Nenhuma água registada hoje\\.\nUsa `/agua 250` para adicionar\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        else:
            liters = total / 1000
            await update.message.reply_text(
                f"💧 *Água hoje:* {liters:.1f} L \\({total} ml\\)",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
