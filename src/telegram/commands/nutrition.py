"""Nutrition command handlers: /comi, /nutricao, /apagar, /preset, and conversation callbacks."""

from __future__ import annotations

import dataclasses
import logging
from datetime import date
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ConversationHandler, ContextTypes

from ..helpers import _is_rate_limited, _parse_date_prefix, safe_command

logger = logging.getLogger(__name__)

_SOURCE_LABELS: dict[str, str] = {
    "openfoodfacts": " _(OpenFoodFacts)_",
    "usda": " _(USDA)_",
    "api_ninjas": " _(API-Ninjas)_",
    "llm_estimate": " _(estimativa IA)_",
}


def _source_label(source: str) -> str:
    """Return a Markdown label for the nutrition data source."""
    return _SOURCE_LABELS.get(source, "")


# ConversationHandler states (shared with bot.py for handler registration)
_AWAITING_CONFIRMATION = 0
_AWAITING_BARCODE_QUANTITY = 1
_AWAITING_PRESET_ITEMS = 2
_AWAITING_EAN_FALLBACK_NAME = 3
_AWAITING_EAN_FALLBACK_QUANTITY = 4


class NutritionMixin:
    """Mixin providing nutrition/food tracking command handlers."""

    async def _cmd_comi(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """/comi [ontem|anteontem|YYYY-MM-DD] <texto|preset> — register food eaten."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return ConversationHandler.END

        from ..formatters import format_food_confirmation, format_meal_preset_confirmation
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
                "Ou usa o nome de um preset guardado:\n"
                "  /comi Lanche\n"
                "  /comi 1.5 Lanche _(multiplica quantidades)_\n\n"
                "Ou pesquisa por código de barras EAN:\n"
                "  /comi ean 5601312308027\n\n"
                "Lista de presets: /preset list"
            )
            return ConversationHandler.END

        context.user_data["pending_date"] = target_date

        # ---- EAN lookup: /comi ean <code> ----
        if args and args[0].lower() == "ean":
            if len(args) < 2:
                await update.message.reply_text("Uso: /comi ean <código>\nExemplo: /comi ean 5601312308027")
                return ConversationHandler.END
            if self._nutrition_service is None:
                await update.message.reply_text(
                    "⚠️ Funcionalidade de nutrição não configurada. Adiciona GROQ_API_KEY ao ficheiro .env."
                )
                return ConversationHandler.END
            ean_code = args[1].strip()
            await update.message.reply_text("🔍 A procurar produto...")
            result = self._nutrition_service.lookup_ean(ean_code)
            if result is None:
                context.user_data["pending_ean_code"] = ean_code
                await update.message.reply_text(
                    f"⚠️ Código *{ean_code}* não encontrado no OpenFoodFacts.\n\n"
                    "Como se chama o produto? Vou estimar os valores nutricionais.",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return _AWAITING_EAN_FALLBACK_NAME
            context.user_data["pending_barcode_item"] = result
            await update.message.reply_text(
                f"Encontrei: *{result.name.title()}*\nQuantas unidades comeste?",
                parse_mode=ParseMode.MARKDOWN,
            )
            return _AWAITING_BARCODE_QUANTITY

        # ---- Check if text matches a saved meal preset (case-insensitive) ----
        preset = self._repo.get_meal_preset_by_name(text)
        multiplier = 1.0

        # If no direct match, try parsing a leading number as a multiplier
        if preset is None and args:
            first_token = args[0].replace(",", ".")
            try:
                candidate = float(first_token)
                if candidate > 0:
                    preset_name_part = " ".join(args[1:]).strip()
                    if preset_name_part:
                        candidate_preset = self._repo.get_meal_preset_by_name(preset_name_part)
                        if candidate_preset is not None:
                            preset = candidate_preset
                            multiplier = candidate
            except ValueError:
                pass

        if preset is not None:
            if not preset.items:
                await update.message.reply_text(f"❌ O preset \"{preset.name}\" está vazio.")
                return ConversationHandler.END
            context.user_data["pending_preset"] = preset
            context.user_data["pending_preset_multiplier"] = multiplier
            msg = format_meal_preset_confirmation(preset.name, preset.items, multiplier=multiplier)
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
            from ...nutrition.service import FoodItemResult
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

        # Single item with unit="un" — user didn't specify grams, so ask before confirming
        if len(items) == 1 and items[0].unit == "un":
            try:
                nutrition = self._nutrition_service.get_nutrition_per_100g(items[0].name)
            except Exception as exc:
                logger.error("get_nutrition_per_100g failed for '%s': %s", items[0].name, exc, exc_info=True)
                nutrition = None
            if nutrition is not None:
                item_source = items[0].source
                context.user_data["pending_ean_nutrition"] = nutrition
                context.user_data["pending_ean_product_name"] = items[0].name
                context.user_data["pending_item_source"] = item_source
                context.user_data["pending_cache_query"] = normalized_query
                source_label = _source_label(item_source)
                await update.message.reply_text(
                    f"*{nutrition.product_name.title()}*{source_label}\n"
                    f"Valores por 100g: {int(nutrition.calories_per_100g or 0)} kcal | "
                    f"P: {int(nutrition.protein_per_100g or 0)}g | "
                    f"G: {int(nutrition.fat_per_100g or 0)}g | "
                    f"HC: {int(nutrition.carbs_per_100g or 0)}g\n\n"
                    "Quantos gramas comeste? _(1 dose = 100g)_",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return _AWAITING_EAN_FALLBACK_QUANTITY

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
            self._repo.set_food_cache(pending_query, [dataclasses.asdict(item) for item in items])

        date_label = f" ({target_date.strftime('%d/%m/%Y')})" if target_date != date.today() else ""
        msg = f"✅ Registado{date_label}! Total: {int(total_cal)} kcal"
        from ..formatters import format_remaining_macros
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

        multiplier = context.user_data.pop("pending_preset_multiplier", 1.0)
        target_date = context.user_data.pop("pending_date", date.today())

        def _scale(value: float | None) -> float | None:
            return round(value * multiplier, 1) if value is not None else None

        entries = [
            {
                "name": item.name,
                "quantity": round(item.quantity * multiplier, 2),
                "unit": item.unit,
                "calories": _scale(item.calories),
                "protein_g": _scale(item.protein_g),
                "fat_g": _scale(item.fat_g),
                "carbs_g": _scale(item.carbs_g),
                "fiber_g": _scale(item.fiber_g),
                "source": "meal_preset",
            }
            for item in preset.items
        ]
        self._repo.save_food_entries(target_date, entries)
        total_cal = sum(_scale(item.calories) or 0 for item in preset.items)

        date_label = f" ({target_date.strftime('%d/%m/%Y')})" if target_date != date.today() else ""
        mult_label = f" ×{multiplier:g}" if multiplier != 1.0 else ""
        msg = f"✅ Preset \"{preset.name}\"{mult_label} registado{date_label}! Total: {int(total_cal)} kcal"
        from ..formatters import format_remaining_macros
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

        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    async def _cancel_food(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Callback or /cancelar: user cancelled food entry or preset creation."""
        context.user_data.pop("pending_food", None)
        context.user_data.pop("pending_barcode_item", None)
        context.user_data.pop("pending_ean_code", None)
        context.user_data.pop("pending_ean_nutrition", None)
        context.user_data.pop("pending_ean_product_name", None)
        context.user_data.pop("pending_item_source", None)
        context.user_data.pop("pending_preset", None)
        context.user_data.pop("pending_preset_multiplier", None)
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

        from ..formatters import format_meal_presets_list, parse_preset_item_line
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
        from ..formatters import parse_preset_item_line
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

        from ..formatters import format_food_confirmation
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
        from ..formatters import format_food_confirmation
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

        # Recalculate nutrients for the new quantity (scale linearly)
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

    async def _handle_ean_fallback_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """User provided a product name after EAN lookup failed — fetch per-100g data, then ask quantity."""
        product_name = (update.message.text or "").strip()
        if not product_name:
            await update.message.reply_text("Por favor escreve o nome do produto.")
            return _AWAITING_EAN_FALLBACK_NAME

        await update.message.reply_text("⏳ A procurar/estimar valores nutricionais...")
        try:
            nutrition, item_source = self._nutrition_service.get_nutrition_with_source(product_name)
        except Exception as exc:
            logger.error("EAN fallback get_nutrition_with_source failed: %s", exc, exc_info=True)
            await update.message.reply_text("❌ Erro ao estimar valores. Tenta /comi novamente.")
            return ConversationHandler.END

        if nutrition is None:
            await update.message.reply_text(
                "Não consegui encontrar o produto. Tenta /comi com uma descrição mais detalhada."
            )
            return ConversationHandler.END

        context.user_data["pending_ean_nutrition"] = nutrition
        context.user_data["pending_ean_product_name"] = product_name
        context.user_data["pending_item_source"] = item_source
        source_label = _source_label(item_source)
        await update.message.reply_text(
            f"*{nutrition.product_name.title()}*{source_label}\n"
            f"Valores por 100g: {int(nutrition.calories_per_100g or 0)} kcal | "
            f"P: {int(nutrition.protein_per_100g or 0)}g | "
            f"G: {int(nutrition.fat_per_100g or 0)}g | "
            f"HC: {int(nutrition.carbs_per_100g or 0)}g\n\n"
            "Quantos gramas comeste? _(1 dose = 100g)_",
            parse_mode=ParseMode.MARKDOWN,
        )
        return _AWAITING_EAN_FALLBACK_QUANTITY

    async def _handle_ean_fallback_quantity(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """User specified grams for the EAN fallback product — scale and show confirmation."""
        from ..formatters import format_food_confirmation
        text = (update.message.text or "").strip()
        try:
            grams = float(text.replace(",", "."))
            if grams <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Por favor responde com o número de gramas (ex: 20, 150, 0.5).")
            return _AWAITING_EAN_FALLBACK_QUANTITY

        nutrition = context.user_data.pop("pending_ean_nutrition", None)
        product_name = context.user_data.pop("pending_ean_product_name", None)
        ean_code = context.user_data.pop("pending_ean_code", None)
        item_source = context.user_data.pop("pending_item_source", "llm_estimate")

        if nutrition is None:
            await update.message.reply_text("❌ Sessão expirada. Tenta /comi novamente.")
            return ConversationHandler.END

        nutrients = self._nutrition_service._calculate_nutrients(nutrition, grams, "g")
        from ...nutrition.service import FoodItemResult
        item = FoodItemResult(
            name=nutrition.product_name,
            quantity=grams,
            unit="g",
            calories=nutrients.get("calories"),
            protein_g=nutrients.get("protein_g"),
            fat_g=nutrients.get("fat_g"),
            carbs_g=nutrients.get("carbs_g"),
            fiber_g=nutrients.get("fiber_g"),
            source=item_source,
            barcode=ean_code,
        )

        context.user_data["pending_food"] = [item]
        if product_name:
            context.user_data["pending_cache_query"] = product_name.lower().strip()
        msg = format_food_confirmation([item])
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Confirmar", callback_data="food_confirm"),
                InlineKeyboardButton("❌ Cancelar", callback_data="food_cancel"),
            ]
        ])
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
        return _AWAITING_CONFIRMATION

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
            cal = int(deleted.calories) if deleted.calories else "?"
            qty_str = f"{int(deleted.quantity)}" if deleted.unit == "un" else f"{deleted.quantity:g}{deleted.unit}"
            await update.message.reply_text(
                f"🗑 Apagada última entrada: *{deleted.name.title()} ({qty_str}) — {cal} kcal*",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text("Não há entradas para apagar hoje.")
