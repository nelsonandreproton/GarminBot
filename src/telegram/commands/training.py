"""Training command handlers: /treinei, /progresso, /equipamento, /sync_treino, /sync_atividades."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..helpers import _is_rate_limited, _parse_date_prefix, _row_to_metrics, safe_command

logger = logging.getLogger(__name__)


class TrainingMixin:
    """Mixin providing training/workout command handlers."""

    @safe_command
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

    @safe_command
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

    @safe_command
    async def _cmd_progresso(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/progresso <exercício> — show training history for a given exercise."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        from ..formatters import format_training_progression
        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Indica o exercício\\. Exemplo: `/progresso bench press`",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return
        exercise = " ".join(args)
        entries = self._repo.search_training_entries(exercise, limit=30)
        text = format_training_progression(exercise, entries)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)

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

        from ..formatters import format_error_message, format_workout_section

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
        from ...training.recommender import generate_workout
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

    @safe_command
    async def _cmd_sync_atividades(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sync_atividades [hoje] — fetch Garmin activities and save to training log."""
        if not self._auth_check(update) or _is_rate_limited(update.effective_chat.id):
            return
        if self._garmin_client is None:
            await update.message.reply_text("Cliente Garmin não configurado.")
            return

        from ..formatters import format_activity_sync
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
