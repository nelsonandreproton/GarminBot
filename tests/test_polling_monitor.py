"""Tests for PollingMonitor: Conflict detection, throttled alerts, health flag."""

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import Conflict, NetworkError

from src.telegram.polling_monitor import PollingMonitor

CHAT_ID = 12345
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self):
        self.current = T0

    def __call__(self):
        return self.current

    def advance(self, delta: timedelta):
        self.current += delta


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def monitor(clock):
    return PollingMonitor(chat_id=CHAT_ID, now=clock)


def make_context(error, webhook_url=""):
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.get_webhook_info = AsyncMock(return_value=SimpleNamespace(url=webhook_url))
    return SimpleNamespace(error=error, bot=bot)


class TestConflictAlert:
    @pytest.mark.asyncio
    async def test_on_error_conflict_sends_alert(self, monitor):
        ctx = make_context(Conflict("terminated by other getUpdates request"))
        await monitor.on_error(None, ctx)
        ctx.bot.send_message.assert_awaited_once()
        kwargs = ctx.bot.send_message.await_args.kwargs
        assert kwargs["chat_id"] == CHAT_ID
        assert "conflito" in kwargs["text"]

    @pytest.mark.asyncio
    async def test_on_error_conflict_sends_without_parse_mode(self, monitor):
        ctx = make_context(Conflict("x"), webhook_url="https://evil.example/<b>*_")
        await monitor.on_error(None, ctx)
        assert "parse_mode" not in ctx.bot.send_message.await_args.kwargs

    @pytest.mark.asyncio
    async def test_on_error_second_conflict_within_interval_does_not_realert(self, monitor, clock):
        ctx = make_context(Conflict("x"))
        await monitor.on_error(None, ctx)
        clock.advance(timedelta(minutes=59))
        await monitor.on_error(None, ctx)
        assert ctx.bot.send_message.await_count == 1

    @pytest.mark.asyncio
    async def test_on_error_conflict_after_interval_realerts(self, monitor, clock):
        ctx = make_context(Conflict("x"))
        await monitor.on_error(None, ctx)
        clock.advance(timedelta(hours=1))
        await monitor.on_error(None, ctx)
        assert ctx.bot.send_message.await_count == 2

    @pytest.mark.asyncio
    async def test_on_error_webhook_url_set_included_in_alert(self, monitor):
        ctx = make_context(Conflict("x"), webhook_url="https://evil.example/hook")
        await monitor.on_error(None, ctx)
        text = ctx.bot.send_message.await_args.kwargs["text"]
        assert "Webhook ativo para: https://evil.example/hook" in text

    @pytest.mark.asyncio
    async def test_on_error_webhook_url_empty_omitted_from_alert(self, monitor):
        ctx = make_context(Conflict("x"), webhook_url="")
        await monitor.on_error(None, ctx)
        text = ctx.bot.send_message.await_args.kwargs["text"]
        assert "Webhook ativo" not in text

    @pytest.mark.asyncio
    async def test_on_error_get_webhook_info_raises_still_sends_alert(self, monitor):
        ctx = make_context(Conflict("x"))
        ctx.bot.get_webhook_info.side_effect = NetworkError("boom")
        await monitor.on_error(None, ctx)
        ctx.bot.send_message.assert_awaited_once()
        assert "Webhook ativo" not in ctx.bot.send_message.await_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_on_error_send_fails_does_not_raise_and_retries_next_conflict(self, monitor, clock):
        ctx = make_context(Conflict("x"))
        ctx.bot.send_message.side_effect = NetworkError("down")
        await monitor.on_error(None, ctx)
        assert monitor.last_conflict_at == T0
        # Alert not recorded, so the very next conflict retries immediately
        ctx.bot.send_message.side_effect = None
        clock.advance(timedelta(seconds=5))
        await monitor.on_error(None, ctx)
        assert ctx.bot.send_message.await_count == 2


class TestOtherErrors:
    @pytest.mark.asyncio
    async def test_on_error_non_conflict_logs_and_does_not_send(self, monitor, caplog):
        err = NetworkError("flaky")
        ctx = make_context(err)
        with caplog.at_level(logging.ERROR):
            await monitor.on_error(None, ctx)
        ctx.bot.send_message.assert_not_awaited()
        assert monitor.last_conflict_at is None
        record = next(r for r in caplog.records if "Unhandled exception" in r.getMessage())
        assert record.exc_info[1] is err


class TestHealth:
    def test_is_healthy_initially_true(self, monitor):
        assert monitor.last_conflict_at is None
        assert monitor.is_healthy() is True

    @pytest.mark.asyncio
    async def test_is_healthy_conflict_within_window_false(self, monitor, clock):
        await monitor.on_error(None, make_context(Conflict("x")))
        clock.advance(timedelta(minutes=9))
        assert monitor.is_healthy() is False

    @pytest.mark.asyncio
    async def test_is_healthy_conflict_older_than_window_true(self, monitor, clock):
        await monitor.on_error(None, make_context(Conflict("x")))
        clock.advance(timedelta(minutes=10))
        assert monitor.is_healthy() is True
