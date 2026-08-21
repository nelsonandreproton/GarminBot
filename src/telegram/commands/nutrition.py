"""Nutrition command handlers: /nutricao and /apagar.

Manual food entry (/comi, /preset, barcode/EAN scanning, LLM text parsing,
the food cache) was removed — all nutrition is now logged via FatSecret and
synced in through /sync and /hoje.
"""

from __future__ import annotations

import logging
from datetime import date

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..helpers import _is_rate_limited, safe_command

logger = logging.getLogger(__name__)


class NutritionMixin:
    """Mixin providing nutrition command handlers."""

    @safe_command
    async def _cmd_nutricao(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/nutricao (alias /dieta) — daily nutrition summary."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        from ..formatters import format_nutrition_day
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

    @safe_command
    async def _cmd_apagar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/apagar — delete last food entry today."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        deleted = self._repo.delete_last_food_entry(date.today())
        if deleted:
            from ..formatters import _escape_md
            cal = int(deleted.calories) if deleted.calories else "?"
            qty_str = f"{int(deleted.quantity)}" if deleted.unit == "un" else f"{deleted.quantity:g}{deleted.unit}"
            name = _escape_md(deleted.name.title())
            await update.message.reply_text(
                f"🗑 Apagada última entrada: *{name} ({qty_str}) — {cal} kcal*",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text("Não há entradas para apagar hoje.")
