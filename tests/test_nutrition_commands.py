"""Tests for nutrition command handlers: /apagar (Prove-It TDD)."""

from __future__ import annotations

import os
import tempfile
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Config
from src.database.repository import Repository
from src.telegram.bot import TelegramBot


def _make_config(**overrides):
    defaults = {
        "telegram_bot_token": "fake-token",
        "telegram_chat_id": "123456",
        "garmin_email": "test@example.com",
        "garmin_password": "secret",
        "database_path": ":memory:",
        "log_level": "INFO",
        "log_file": None,
        "daily_alerts": False,
        "groq_api_key": None,
        "fatsecret_consumer_key": None,
        "fatsecret_consumer_secret": None,
        "garmin_api_port": None,
        "garmin_api_key": None,
        "health_port": None,
        "newsletter_enabled": False,
    }
    defaults.update(overrides)
    cfg = MagicMock(spec=Config)
    for k, v in defaults.items():
        setattr(cfg, k, v)
    return cfg


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    try:
        os.unlink(path)
    except PermissionError:
        pass


@pytest.fixture
def repo(db_path):
    r = Repository(db_path)
    r.init_database()
    yield r
    r._engine.dispose()


_NEXT_CHAT_ID = 800000


def _make_update(chat_id=None):
    """Create a mock update with a unique chat_id to avoid rate-limit collisions."""
    global _NEXT_CHAT_ID
    if chat_id is None:
        _NEXT_CHAT_ID += 1
        chat_id = _NEXT_CHAT_ID
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message = AsyncMock()
    update.message.reply_text = AsyncMock()
    return update


def _make_context():
    ctx = MagicMock()
    ctx.args = []
    return ctx


def _make_bot(repo, chat_id):
    cfg = _make_config()
    cfg.telegram_chat_id = str(chat_id)
    bot = TelegramBot(cfg, repo)
    bot._chat_id = chat_id
    return bot


class TestCmdApagarEscapesMarkdown:
    """Prove-It: a FatSecret food name containing Markdown special characters
    must not be interpolated raw into a ParseMode.MARKDOWN reply — Telegram
    raises BadRequest on malformed Markdown, which safe_command swallows into
    a generic error, hiding that the delete already succeeded (real risk: a
    user retries /apagar believing it failed, deleting a second valid entry)."""

    @pytest.mark.asyncio
    async def test_food_name_with_underscore_is_escaped_in_reply(self, repo):
        repo.save_food_entries(date.today(), [{
            "name": "whey_protein* [vanilla]", "quantity": 1, "unit": "un",
            "calories": 120.0, "protein_g": 20.0, "fat_g": 2.0, "carbs_g": 3.0,
            "fiber_g": 0.0, "source": "fatsecret",
        }])
        update = _make_update()
        bot = _make_bot(repo, update.effective_chat.id)

        await bot._cmd_apagar(update, _make_context())

        sent_text = update.message.reply_text.call_args[0][0]
        assert "Whey\\_Protein\\* \\[Vanilla]" in sent_text

    @pytest.mark.asyncio
    async def test_plain_food_name_reply_unaffected(self, repo):
        repo.save_food_entries(date.today(), [{
            "name": "ovo cozido", "quantity": 2, "unit": "un",
            "calories": 140.0, "protein_g": 12.0, "fat_g": 10.0, "carbs_g": 1.0,
            "fiber_g": 0.0, "source": "fatsecret",
        }])
        update = _make_update()
        bot = _make_bot(repo, update.effective_chat.id)

        await bot._cmd_apagar(update, _make_context())

        sent_text = update.message.reply_text.call_args[0][0]
        assert "Ovo Cozido" in sent_text
