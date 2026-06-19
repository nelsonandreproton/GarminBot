"""Tests for health command handlers: /hoje live FatSecret fetch and show_budget wiring."""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.database.repository import Repository
from src.config import Config
from src.telegram.bot import TelegramBot


# ---------------------------------------------------------------------------
# Minimal config stub
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
    activity = MagicMock()
    activity.steps = 8500
    activity.active_calories = 600
    activity.resting_calories = 1800
    activity.total_calories = 2400
    client.get_activity_data.return_value = activity
    client.get_health_data.return_value = {}
    return client


@pytest.fixture
def fatsecret_client():
    client = MagicMock()
    client.get_food_entries.return_value = []
    return client


def _make_bot(repo, garmin_client=None, fatsecret_client=None, chat_id=None):
    cfg = _make_config()
    if chat_id is not None:
        cfg.telegram_chat_id = str(chat_id)
    bot = TelegramBot(cfg, repo, garmin_client=garmin_client, fatsecret_client=fatsecret_client)
    if chat_id is not None:
        bot._chat_id = chat_id
    return bot


_NEXT_CHAT_ID = 900000

def _make_update(chat_id=None):
    """Create a mock update with a unique chat_id to avoid rate limit collisions."""
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


# ---------------------------------------------------------------------------
# TelegramBot constructor
# ---------------------------------------------------------------------------

class TestTelegramBotConstructor:
    def test_fatsecret_client_stored(self, repo):
        fs = MagicMock()
        bot = _make_bot(repo, fatsecret_client=fs, chat_id=100001)
        assert bot._fatsecret_client is fs

    def test_fatsecret_client_defaults_to_none(self, repo):
        bot = _make_bot(repo, chat_id=100002)
        assert bot._fatsecret_client is None

    def test_garmin_client_still_works(self, repo, garmin_client):
        bot = _make_bot(repo, garmin_client=garmin_client, chat_id=100003)
        assert bot._garmin_client is garmin_client


# ---------------------------------------------------------------------------
# /hoje: live FatSecret fetch + upsert
# ---------------------------------------------------------------------------

class TestCmdHojeFatSecretFetch:
    @pytest.mark.asyncio
    async def test_fatsecret_get_food_entries_called_with_today(self, repo, garmin_client, fatsecret_client):
        """When fatsecret_client is present, get_food_entries must be called with today."""
        update = _make_update()
        bot = _make_bot(repo, garmin_client=garmin_client, fatsecret_client=fatsecret_client,
                        chat_id=update.effective_chat.id)
        today = date.today()

        with patch.object(bot, "send_daily_summary", new_callable=AsyncMock):
            await bot._cmd_hoje(update, _make_context())

        fatsecret_client.get_food_entries.assert_called_once_with(today)

    @pytest.mark.asyncio
    async def test_fatsecret_upsert_called_after_fetch(self, repo, garmin_client, fatsecret_client):
        """Upsert must be called with today's mapped entries."""
        mapped = [
            {"name": "Banana", "calories": 100.0, "protein_g": 1.0,
             "fat_g": 0.3, "carbs_g": 25.0, "fiber_g": 2.0,
             "quantity": 1.0, "unit": "serving", "source": "fatsecret", "barcode": "FS001"},
        ]
        fatsecret_client.get_food_entries.return_value = [{"raw": "entry"}]
        update = _make_update()
        bot = _make_bot(repo, garmin_client=garmin_client, fatsecret_client=fatsecret_client,
                        chat_id=update.effective_chat.id)
        today = date.today()

        with patch("src.telegram.commands.health.map_fatsecret_entries", return_value=mapped) as mock_map, \
             patch.object(repo, "upsert_fatsecret_entries") as mock_upsert, \
             patch.object(bot, "send_daily_summary", new_callable=AsyncMock):
            await bot._cmd_hoje(update, _make_context())

        mock_upsert.assert_called_once_with(today, mapped)

    @pytest.mark.asyncio
    async def test_fatsecret_not_called_when_client_none(self, repo, garmin_client):
        """No FatSecret call when client is None."""
        update = _make_update()
        bot = _make_bot(repo, garmin_client=garmin_client, fatsecret_client=None,
                        chat_id=update.effective_chat.id)

        with patch.object(bot, "send_daily_summary", new_callable=AsyncMock) as mock_send:
            await bot._cmd_hoje(update, _make_context())

        # No crash, sends summary fine
        mock_send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fatsecret_fetch_exception_does_not_break_hoje(self, repo, garmin_client):
        """A FatSecret exception must NOT break /hoje — Garmin data still shown."""
        fatsecret = MagicMock()
        fatsecret.get_food_entries.side_effect = RuntimeError("API down")
        update = _make_update()
        bot = _make_bot(repo, garmin_client=garmin_client, fatsecret_client=fatsecret,
                        chat_id=update.effective_chat.id)

        with patch.object(bot, "send_daily_summary", new_callable=AsyncMock) as mock_send:
            await bot._cmd_hoje(update, _make_context())

        # /hoje must complete and send the daily summary despite the FatSecret error
        mock_send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fatsecret_exception_logged_as_warning(self, repo, garmin_client, caplog):
        """FatSecret exception must be logged as warning, not error."""
        fatsecret = MagicMock()
        fatsecret.get_food_entries.side_effect = ConnectionError("timeout")
        update = _make_update()
        bot = _make_bot(repo, garmin_client=garmin_client, fatsecret_client=fatsecret,
                        chat_id=update.effective_chat.id)

        with patch.object(bot, "send_daily_summary", new_callable=AsyncMock), \
             caplog.at_level(logging.WARNING):
            await bot._cmd_hoje(update, _make_context())

        assert any("fatsecret" in r.message.lower() or "FatSecret" in r.message
                   for r in caplog.records)


# ---------------------------------------------------------------------------
# /hoje: show_budget present; /ontem: NOT present
# ---------------------------------------------------------------------------

class TestShowBudgetFlag:
    @pytest.mark.asyncio
    async def test_hoje_calls_send_daily_summary_with_show_budget_true(self, repo, garmin_client):
        """_cmd_hoje must pass show_budget=True to send_daily_summary."""
        update = _make_update()
        bot = _make_bot(repo, garmin_client=garmin_client, chat_id=update.effective_chat.id)

        with patch.object(bot, "send_daily_summary", new_callable=AsyncMock) as mock_send:
            await bot._cmd_hoje(update, _make_context())

        call_kwargs = mock_send.call_args
        assert call_kwargs is not None
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        assert kwargs.get("show_budget") is True

    @pytest.mark.asyncio
    async def test_ontem_does_not_pass_show_budget_true(self, repo):
        """_cmd_ontem must NOT pass show_budget=True (defaults to False)."""
        row = MagicMock()
        row.date = date.today()
        row.steps = 7000
        row.active_calories = 500
        row.resting_calories = 1700
        row.total_calories = 2200
        row.sleep_hours = 7.0
        row.sleep_score = 72
        row.sleep_quality = "Good"
        for attr in ["sleep_deep_min", "sleep_light_min", "sleep_rem_min",
                     "resting_heart_rate", "avg_stress", "body_battery_high",
                     "body_battery_low", "spo2_avg", "weight_kg",
                     "floors_ascended", "intensity_moderate_min", "intensity_vigorous_min"]:
            setattr(row, attr, None)

        update = _make_update()
        bot = _make_bot(repo, chat_id=update.effective_chat.id)

        with patch.object(repo, "get_metrics_by_date", return_value=row), \
             patch.object(repo, "get_daily_nutrition", return_value={"entry_count": 0}), \
             patch.object(bot, "send_daily_summary", new_callable=AsyncMock) as mock_send:
            await bot._cmd_ontem(update, _make_context())

        call_kwargs = mock_send.call_args
        assert call_kwargs is not None
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        # show_budget must NOT be True
        assert kwargs.get("show_budget", False) is False


# ---------------------------------------------------------------------------
# send_daily_summary: budget block present when show_budget=True
# ---------------------------------------------------------------------------

class TestSendDailySummaryBudgetBlock:
    @pytest.mark.asyncio
    async def test_budget_block_in_hoje_output(self, repo, garmin_client):
        """The Orçamento line must appear inside the Nutrição section of /hoje output.
        Gasto and Comido lines must NOT appear (they were removed).
        """
        today = date.today()
        # Insert a food entry so get_daily_nutrition returns entry_count >= 1 and
        # the Nutrição section is rendered (required for the Orçamento line to appear).
        repo.save_food_entries(today, [{
            "name": "Teste",
            "calories": 237.0,
            "protein_g": 33.0,
            "fat_g": 6.0,
            "carbs_g": 9.0,
            "fiber_g": 0.0,
        }])

        update = _make_update()
        bot = _make_bot(repo, garmin_client=garmin_client, chat_id=update.effective_chat.id)

        sent_texts: list[str] = []

        async def capture_send(text, chat_id=None):
            sent_texts.append(text)

        with patch.object(bot, "_send", side_effect=capture_send):
            await bot._cmd_hoje(update, _make_context())

        all_text = "\n".join(sent_texts)
        # Orçamento line must appear (inside Nutrição section)
        assert "Orçamento" in all_text
        # Gasto and Comido lines must be gone
        assert "Gasto" not in all_text
        assert "Comido" not in all_text
        # Orçamento must appear after Défice line when both are present
        if "Défice" in all_text:
            assert all_text.index("Défice") < all_text.index("Orçamento")

    @pytest.mark.asyncio
    async def test_budget_block_absent_in_scheduled_morning_report(self, repo):
        """The morning report (send_daily_summary default) must NOT show budget block."""
        bot = _make_bot(repo, chat_id=800001)
        metrics = {
            "date": date.today(),
            "steps": 7000,
            "active_calories": 500,
            "resting_calories": 1700,
            "total_calories": 2200,
        }

        sent_texts: list[str] = []

        async def capture_send(text, chat_id=None):
            sent_texts.append(text)

        with patch.object(bot, "_send", side_effect=capture_send), \
             patch.object(repo, "get_weekly_stats", return_value=None), \
             patch.object(repo, "get_goals", return_value={}), \
             patch.object(repo, "get_metrics_range", return_value=[]), \
             patch.object(repo, "get_daily_water", return_value=0):
            # Default call: no show_budget
            await bot.send_daily_summary(metrics)

        all_text = "\n".join(sent_texts)
        # Budget block markers must NOT appear in scheduled morning report
        assert "Orçamento" not in all_text
