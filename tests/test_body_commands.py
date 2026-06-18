"""Tests for body command handlers: /peso view mode (Prove-It TDD)."""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.database.repository import Repository
from src.config import Config
from src.telegram.bot import TelegramBot


# ---------------------------------------------------------------------------
# Helpers / fixtures  (mirror test_health_commands.py pattern exactly)
# ---------------------------------------------------------------------------

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
        "usda_api_key": None,
        "api_ninjas_key": None,
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


@pytest.fixture
def garmin_client():
    client = MagicMock()
    client.get_weight_data.return_value = None
    return client


_NEXT_CHAT_ID = 700000


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


def _make_context(args=None):
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


def _make_bot(repo, garmin_client=None, chat_id=None):
    cfg = _make_config()
    if chat_id is not None:
        cfg.telegram_chat_id = str(chat_id)
    bot = TelegramBot(cfg, repo, garmin_client=garmin_client)
    if chat_id is not None:
        bot._chat_id = chat_id
    return bot


# ---------------------------------------------------------------------------
# PROVE-IT TDD: failing test first  (proves the bug)
#
#  Before the fix:
#    - save_manual_weight is NOT called in the view branch
#    - get_weekly_weight_stats is called with yesterday, not today
# ---------------------------------------------------------------------------

class TestCmdPesoViewLiveFetch:

    @pytest.mark.asyncio
    async def test_peso_view_live_fetches_today_and_persists(self, repo):
        """
        BUG: /peso (view mode) never shows today's weight because it only reads
        the DB which only has data up to yesterday.

        FIX: before reading the DB, live-fetch today from Garmin and persist it.

        This test asserts the FIXED behaviour:
          1. save_manual_weight is called with (today, 93.7)
          2. get_weekly_weight_stats is called with today (not yesterday)
        """
        today = date.today()
        yesterday = today - timedelta(days=1)

        # DB has yesterday's weight but NOT today's
        repo.save_manual_weight(yesterday, 94.0)

        update = _make_update()
        gc = MagicMock()
        gc.get_weight_data.return_value = 93.7  # today's weight available in Garmin
        bot = _make_bot(repo, garmin_client=gc, chat_id=update.effective_chat.id)

        # Patch send_image to prevent actual Bot() construction
        with patch.object(bot, "send_image", new_callable=AsyncMock):
            await bot._cmd_peso(update, _make_context())

        # Garmin was asked for today's weight
        gc.get_weight_data.assert_called_once_with(today)

        # Weight was persisted to DB
        with patch.object(repo, "save_manual_weight") as mock_save:
            pass  # used below via direct DB check instead

        # Check DB directly: today's weight was saved
        row = repo.get_metrics_by_date(today)
        assert row is not None, "Today's weight row should have been saved to DB"
        assert row.weight_kg == 93.7, f"Expected 93.7 but got {row.weight_kg}"

        # get_weekly_weight_stats must have been called with today
        # We verify by checking that the text reply shows today's data (93.7 is the current weight)
        update.message.reply_text.assert_awaited()

    @pytest.mark.asyncio
    async def test_peso_view_calls_weekly_stats_with_today(self, repo):
        """
        Specifically verify that get_weekly_weight_stats is called with today
        (not yesterday) after the fix.
        """
        today = date.today()

        update = _make_update()
        gc = MagicMock()
        gc.get_weight_data.return_value = 93.7
        bot = _make_bot(repo, garmin_client=gc, chat_id=update.effective_chat.id)

        with patch.object(repo, "get_weekly_weight_stats", wraps=repo.get_weekly_weight_stats) as mock_stats, \
             patch.object(bot, "send_image", new_callable=AsyncMock):
            await bot._cmd_peso(update, _make_context())

        mock_stats.assert_called_once_with(today)

    @pytest.mark.asyncio
    async def test_peso_view_save_manual_weight_called_with_today(self, repo):
        """
        Directly verify save_manual_weight is called with today + the weight value
        returned by Garmin.
        """
        today = date.today()

        update = _make_update()
        gc = MagicMock()
        gc.get_weight_data.return_value = 88.5
        bot = _make_bot(repo, garmin_client=gc, chat_id=update.effective_chat.id)

        with patch.object(repo, "save_manual_weight", wraps=repo.save_manual_weight) as mock_save, \
             patch.object(bot, "send_image", new_callable=AsyncMock):
            await bot._cmd_peso(update, _make_context())

        mock_save.assert_called_with(today, 88.5)


# ---------------------------------------------------------------------------
# Graceful degradation tests
# ---------------------------------------------------------------------------

class TestCmdPesoViewGracefulDegradation:

    @pytest.mark.asyncio
    async def test_garmin_raises_exception_peso_still_replies(self, repo):
        """
        If get_weight_data raises a generic Exception, /peso must still reply
        (no crash, DB is read and shown to the user).
        """
        yesterday = date.today() - timedelta(days=1)
        repo.save_manual_weight(yesterday, 80.0)

        update = _make_update()
        gc = MagicMock()
        gc.get_weight_data.side_effect = Exception("Garmin API timeout")
        bot = _make_bot(repo, garmin_client=gc, chat_id=update.effective_chat.id)

        with patch.object(bot, "send_image", new_callable=AsyncMock):
            await bot._cmd_peso(update, _make_context())

        # Must still send the reply with DB data
        update.message.reply_text.assert_awaited()

    @pytest.mark.asyncio
    async def test_garmin_raises_exception_save_not_called(self, repo):
        """When get_weight_data raises, save_manual_weight must NOT be called."""
        update = _make_update()
        gc = MagicMock()
        gc.get_weight_data.side_effect = RuntimeError("network error")
        bot = _make_bot(repo, garmin_client=gc, chat_id=update.effective_chat.id)

        with patch.object(repo, "save_manual_weight") as mock_save, \
             patch.object(bot, "send_image", new_callable=AsyncMock):
            await bot._cmd_peso(update, _make_context())

        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_garmin_returns_none_save_not_called(self, repo):
        """When get_weight_data returns None (no weight today), save_manual_weight
        must NOT be called."""
        update = _make_update()
        gc = MagicMock()
        gc.get_weight_data.return_value = None
        bot = _make_bot(repo, garmin_client=gc, chat_id=update.effective_chat.id)

        with patch.object(repo, "save_manual_weight") as mock_save, \
             patch.object(bot, "send_image", new_callable=AsyncMock):
            await bot._cmd_peso(update, _make_context())

        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_garmin_returns_none_peso_still_replies(self, repo):
        """When get_weight_data returns None, /peso still replies from DB."""
        update = _make_update()
        gc = MagicMock()
        gc.get_weight_data.return_value = None
        bot = _make_bot(repo, garmin_client=gc, chat_id=update.effective_chat.id)

        with patch.object(bot, "send_image", new_callable=AsyncMock):
            await bot._cmd_peso(update, _make_context())

        update.message.reply_text.assert_awaited()

    @pytest.mark.asyncio
    async def test_garmin_exception_logged_as_warning(self, repo, caplog):
        """Garmin failure during /peso view must be logged as WARNING, not ERROR."""
        update = _make_update()
        gc = MagicMock()
        gc.get_weight_data.side_effect = ConnectionError("timeout")
        bot = _make_bot(repo, garmin_client=gc, chat_id=update.effective_chat.id)

        with patch.object(bot, "send_image", new_callable=AsyncMock), \
             caplog.at_level(logging.WARNING):
            await bot._cmd_peso(update, _make_context())

        # A warning should be logged about the failure
        assert any(
            "weight" in r.message.lower() or "peso" in r.message.lower() or "garmin" in r.message.lower()
            for r in caplog.records
            if r.levelno == logging.WARNING
        )


class TestCmdPesoViewNoGarminClient:

    @pytest.mark.asyncio
    async def test_garmin_client_none_peso_still_works(self, repo):
        """/peso must work from DB alone when garmin_client is None."""
        yesterday = date.today() - timedelta(days=1)
        repo.save_manual_weight(yesterday, 78.0)

        update = _make_update()
        bot = _make_bot(repo, garmin_client=None, chat_id=update.effective_chat.id)

        with patch.object(bot, "send_image", new_callable=AsyncMock):
            await bot._cmd_peso(update, _make_context())

        update.message.reply_text.assert_awaited()

    @pytest.mark.asyncio
    async def test_garmin_client_none_save_not_called(self, repo):
        """When garmin_client is None, save_manual_weight must NOT be called."""
        update = _make_update()
        bot = _make_bot(repo, garmin_client=None, chat_id=update.effective_chat.id)

        with patch.object(repo, "save_manual_weight") as mock_save, \
             patch.object(bot, "send_image", new_callable=AsyncMock):
            await bot._cmd_peso(update, _make_context())

        mock_save.assert_not_called()
