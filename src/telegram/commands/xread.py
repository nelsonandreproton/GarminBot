"""Command handler: /xread <url> — fetch and analyse a Twitter/X post."""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..helpers import safe_command

logger = logging.getLogger(__name__)


class XreadMixin:
    """Mixin providing the /xread command handler."""

    @safe_command
    async def _cmd_xread(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/xread <url> — fetch a Twitter/X post and save insights to Obsidian."""
        if not self._auth_check(update):
            return

        args = context.args or []
        if not args:
            await update.message.reply_text(
                "Usa: `/xread <url>`\nExemplo: `/xread https://x.com/user/status/123`",
            )
            return

        url = args[0].strip()
        if not ("x.com" in url or "twitter.com" in url):
            await update.message.reply_text("URL inválido. Envia um link de x.com ou twitter.com.")
            return

        if self._xread_callback is None:
            await update.message.reply_text("Xread não configurado (OBSIDIAN_VAULT_PATH ou GROQ_API_KEY em falta?).")
            return

        await update.message.reply_text("⏳ A analisar tweet...")

        loop = asyncio.get_event_loop()
        try:
            title, takeaways = await loop.run_in_executor(None, self._xread_callback, url)
        except Exception as exc:
            logger.error("xread failed for %s: %s", url, exc)
            await update.message.reply_text(f"❌ Erro ao processar o tweet:\n{exc}")
            return

        takeaways_text = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(takeaways))
        await update.message.reply_text(
            f"*{title}*\n\n{takeaways_text}\n\n✅ Guardado no Obsidian.",
        )
