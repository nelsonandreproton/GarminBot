"""Telegram bot: core class, sending infrastructure, and application setup.

Command handlers live in src/telegram/commands/ as mixin classes.
Shared helpers (rate limiting, date parsing, row conversion) are in helpers.py.
"""

from __future__ import annotations

import logging
import warnings
from datetime import date, timedelta
from typing import Any, Callable

from telegram import Bot, BotCommand, Update
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
    format_weekly_report,
)
from .helpers import _is_rate_limited, _parse_date_prefix, _row_to_metrics  # noqa: F401 (re-exported)
from .commands import (
    BodyMixin,
    HealthMixin,
    NutritionMixin,
    SystemMixin,
    TrainingMixin,
    XreadMixin,
    _AWAITING_BARCODE_QUANTITY,
    _AWAITING_CONFIRMATION,
    _AWAITING_EAN_FALLBACK_NAME,
    _AWAITING_EAN_FALLBACK_QUANTITY,
    _AWAITING_PRESET_ITEMS,
)

logger = logging.getLogger(__name__)


def _on_send_retry(retry_state) -> None:
    logger.warning("Telegram send attempt %d failed: %s", retry_state.attempt_number, retry_state.outcome.exception())


class TelegramBot(HealthMixin, BodyMixin, NutritionMixin, TrainingMixin, SystemMixin, XreadMixin):
    """Wraps python-telegram-bot for sending messages and handling commands.

    Command implementations are split across mixin classes:
      - HealthMixin  → /hoje /ontem /semana /mes /historico
      - BodyMixin    → /peso /sync_peso /barriga /agua /objetivo
      - NutritionMixin → /comi /nutricao /apagar /preset (+ conversation)
      - TrainingMixin → /treinei /progresso /equipamento /sync_treino /sync_atividades
      - SystemMixin  → /sync /status /ajuda /exportar /backfill
      - XreadMixin   → /xread
    """

    def __init__(
        self,
        config: Config,
        repository: Repository,
        garmin_sync_callback: Callable | None = None,
        garmin_backfill_callback: Callable | None = None,
        garmin_client=None,
    ) -> None:
        self._config = config
        self._repo = repository
        self._chat_id = int(config.telegram_chat_id)
        self._garmin_sync = garmin_sync_callback
        self._garmin_backfill = garmin_backfill_callback
        self._garmin_client = garmin_client
        self._garmin_report: Callable | None = None    # Set by main.py after bot creation
        self._newsletter_check: Callable | None = None  # Set by main.py if newsletter enabled
        self._newsletter_bulk: Callable | None = None   # Set by main.py if newsletter enabled
        self._xread_callback: Callable | None = None    # Set by main.py if xread enabled
        self._app: Application | None = None
        # NutritionService (lazy init — only if GROQ_API_KEY is set)
        self._nutrition_service = None
        if config.groq_api_key:
            from ..nutrition.service import NutritionService
            self._nutrition_service = NutritionService(
                config.groq_api_key,
                usda_api_key=config.usda_api_key,
                api_ninjas_key=config.api_ninjas_key,
            )

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
        # Inject daily water total if not already set
        if "water_ml" not in metrics:
            water = self._repo.get_daily_water(day)
            if water > 0:
                metrics["water_ml"] = water
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
        water_weekly_avg_ml: float | None = None,
    ) -> None:
        """Send the weekly report message with week-over-week comparison, weight, and nutrition.

        Args:
            stats: Weekly stats dict from Repository.get_weekly_stats().
            weight_stats: Optional weekly weight stats.
            weekly_nutrition: Pre-computed weekly nutrition dict (with optional avg_deficit).
                              If None, it is fetched from the repository.
            water_weekly_avg_ml: Optional average daily ml of water for the week.
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
            water_weekly_avg_ml=water_weekly_avg_ml,
        )
        await self._send(text)
        logger.info("Weekly report sent")

    async def send_error(self, context: str, error: Exception) -> None:
        """Send an error notification to the configured chat."""
        try:
            await self._send(format_error_message(context, error))
        except Exception as exc:
            logger.error("Failed to send error notification: %s", exc)

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

    # ------------------------------------------------------------------ #
    # Auth                                                                  #
    # ------------------------------------------------------------------ #

    def _auth_check(self, update: Update) -> bool:
        """Return True if the message is from the authorized chat."""
        if update.effective_chat is None:
            return False
        return update.effective_chat.id == self._chat_id

    # ------------------------------------------------------------------ #
    # Application lifecycle                                                #
    # ------------------------------------------------------------------ #

    def build_application(self) -> Application:
        """Build and configure the telegram Application with all command handlers."""
        app = Application.builder().token(self._config.telegram_bot_token).build()

        # Defense-in-depth: drop all messages from unauthorized chats at the
        # framework level, before any handler code runs.  Each handler still
        # calls _auth_check() internally, so this is an extra safety net.
        chat_filter = filters.Chat(chat_id=self._chat_id)

        def _cmd(command: str, handler):
            return CommandHandler(command, handler, filters=chat_filter)

        app.add_handler(_cmd("hoje", self._cmd_hoje))
        app.add_handler(_cmd("ontem", self._cmd_ontem))
        app.add_handler(_cmd("semana", self._cmd_semana))
        app.add_handler(_cmd("mes", self._cmd_mes))
        app.add_handler(_cmd("sync", self._cmd_sync))
        app.add_handler(_cmd("pump", self._cmd_pump))
        app.add_handler(_cmd("xread", self._cmd_xread))
        app.add_handler(_cmd("status", self._cmd_status))
        app.add_handler(_cmd("ajuda", self._cmd_ajuda))
        app.add_handler(_cmd("help", self._cmd_ajuda))
        app.add_handler(_cmd("historico", self._cmd_historico))
        app.add_handler(_cmd("exportar", self._cmd_exportar))
        app.add_handler(_cmd("backfill", self._cmd_backfill))
        app.add_handler(_cmd("objetivo", self._cmd_objetivo))
        app.add_handler(_cmd("peso", self._cmd_peso))
        app.add_handler(_cmd("sync_peso", self._cmd_sync_peso))
        app.add_handler(_cmd("barriga", self._cmd_barriga))
        app.add_handler(_cmd("agua", self._cmd_agua))
        app.add_handler(_cmd("nutricao", self._cmd_nutricao))
        app.add_handler(_cmd("dieta", self._cmd_nutricao))
        app.add_handler(_cmd("apagar", self._cmd_apagar))
        app.add_handler(_cmd("sync_treino", self._cmd_sync_treino))
        app.add_handler(_cmd("equipamento", self._cmd_equipamento))
        app.add_handler(_cmd("treinei", self._cmd_treinei))
        app.add_handler(_cmd("progresso", self._cmd_progresso))
        app.add_handler(_cmd("sync_atividades", self._cmd_sync_atividades))

        # Nutrition conversation (text entry + barcode + meal presets)
        conv = ConversationHandler(
            entry_points=[
                CommandHandler("comi", self._cmd_comi, filters=chat_filter),
                CommandHandler("preset", self._cmd_preset, filters=chat_filter),
                MessageHandler(filters.PHOTO & chat_filter, self._handle_photo),
            ],
            states={
                _AWAITING_CONFIRMATION: [
                    CallbackQueryHandler(self._confirm_food, pattern="^food_confirm$"),
                    CallbackQueryHandler(self._confirm_preset, pattern="^preset_confirm$"),
                    CallbackQueryHandler(self._cancel_food, pattern="^food_cancel$"),
                ],
                _AWAITING_BARCODE_QUANTITY: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND & chat_filter, self._handle_barcode_quantity),
                ],
                _AWAITING_EAN_FALLBACK_NAME: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND & chat_filter, self._handle_ean_fallback_name),
                ],
                _AWAITING_EAN_FALLBACK_QUANTITY: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND & chat_filter, self._handle_ean_fallback_quantity),
                ],
                _AWAITING_PRESET_ITEMS: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND & chat_filter, self._handle_preset_item),
                    CommandHandler("done", self._cmd_preset_done, filters=chat_filter),
                    CallbackQueryHandler(self._save_preset, pattern="^preset_save$"),
                    CallbackQueryHandler(self._cancel_food, pattern="^food_cancel$"),
                ],
            },
            fallbacks=[CommandHandler("cancelar", self._cancel_food, filters=chat_filter)],
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
            BotCommand("agua", "Registar ou ver ingestão de água (ex: /agua 250)"),
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
            BotCommand("progresso", "Ver histórico de exercício (ex: /progresso bench press)"),
            BotCommand("pump", "Ver insights do artigo de hoje do The Pump"),
            BotCommand("xread", "Analisar tweet e guardar no Obsidian (ex: /xread <url>)"),
            BotCommand("server_status", "Estado atual do servidor Hetzner"),
            BotCommand("container_disk", "Uso de disco por container Docker"),
            BotCommand("canticos", "Cânticos do Caminho (ex: /canticos João 3:16)"),
            BotCommand("canticos_paroquia", "Cânticos da Paróquia (ex: /canticos_paroquia João 3:16)"),
        ]
        await bot.set_my_commands(commands)
        logger.info("Telegram commands registered with BotFather")
