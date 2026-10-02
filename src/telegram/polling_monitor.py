"""Detect Telegram polling Conflicts (foreign webhook / second getUpdates) and alert."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from telegram.error import Conflict

logger = logging.getLogger(__name__)

_ALERT_TEXT = (
    "⚠️ Telegram: conflito no polling — os comandos não estão a chegar ao bot. "
    "Outra instância ou um webhook está a usar o token. "
    "Se não foste tu, o token pode estar comprometido: revoga-o no @BotFather."
)


class PollingMonitor:
    """PTB error handler that alerts (throttled) on polling Conflicts and tracks health."""

    def __init__(
        self,
        chat_id: int,
        alert_interval: timedelta = timedelta(hours=1),
        unhealthy_window: timedelta = timedelta(minutes=10),
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._chat_id = chat_id
        self._alert_interval = alert_interval
        self._unhealthy_window = unhealthy_window
        self._now = now
        self.last_conflict_at: datetime | None = None
        self.last_alert_at: datetime | None = None

    async def on_error(self, update, context) -> None:
        if not isinstance(context.error, Conflict):
            logger.error("Unhandled exception in Telegram handler", exc_info=context.error)
            return

        now = self._now()
        self.last_conflict_at = now
        logger.error("Telegram polling conflict: %s", context.error)

        if self.last_alert_at is not None and now - self.last_alert_at < self._alert_interval:
            return

        text = _ALERT_TEXT
        try:
            info = await context.bot.get_webhook_info()
            if info.url:
                text += f"\nWebhook ativo para: {info.url}"
        except Exception:
            logger.exception("Could not fetch webhook info")

        try:
            # Plain text on purpose: the webhook URL is attacker-controlled.
            await context.bot.send_message(chat_id=self._chat_id, text=text)
            self.last_alert_at = now
        except Exception:
            logger.exception("Could not send polling conflict alert")

    def is_healthy(self) -> bool:
        if self.last_conflict_at is None:
            return True
        return self._now() - self.last_conflict_at >= self._unhealthy_window
