"""Scheduler job definitions for manual sync and daily report."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from ..database.repository import Repository
from ..garmin.client import GarminClient
from ..telegram.bot import TelegramBot

logger = logging.getLogger(__name__)


def _run_async(coro) -> None:
    """Run an async coroutine synchronously from a sync context."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(coro)
    finally:
        loop.close()


def make_sync_job(garmin: GarminClient, repo: Repository) -> callable:
    """Return a callable that syncs yesterday's Garmin data to the database.

    Args:
        garmin: Authenticated GarminClient.
        repo: Database repository.

    Returns:
        Callable used by /sync command.
    """
    def sync_yesterday_data_job() -> None:
        logger.info("Sync: starting Garmin sync")
        try:
            summary = garmin.get_yesterday_summary()
            metrics = garmin.to_metrics_dict(summary)
            repo.save_daily_metrics(summary.date, metrics)

            status = "success" if metrics.get("garmin_sync_success") else "partial"
            repo.log_sync(status)
            logger.info("Sync: complete for %s (status=%s)", summary.date, status)
        except Exception as exc:
            repo.log_sync("error", str(exc))
            logger.error("Sync: failed: %s", exc, exc_info=True)
            raise

    return sync_yesterday_data_job


def make_report_callback(repo: Repository, bot: TelegramBot) -> callable:
    """Return a callable that sends yesterday's daily report. Used by /sync command.

    Args:
        repo: Database repository.
        bot: TelegramBot instance.

    Returns:
        Callable that sends the daily report when called.
    """
    def report_callback() -> None:
        yesterday = date.today() - timedelta(days=1)
        row = repo.get_metrics_by_date(yesterday)

        if row is None:
            logger.warning("Daily report: no data for %s", yesterday)
            _run_async(bot.send_error(
                f"relatório de {yesterday}",
                RuntimeError("Sem dados para ontem — o sync falhou ou ainda não correu."),
            ))
            return

        metrics = {
            "date": row.date,
            "sleep_hours": row.sleep_hours,
            "sleep_score": row.sleep_score,
            "sleep_quality": row.sleep_quality,
            "steps": row.steps,
            "active_calories": row.active_calories,
            "resting_calories": row.resting_calories,
            "total_calories": row.total_calories,
            "resting_heart_rate": row.resting_heart_rate,
            "avg_stress": row.avg_stress,
            "body_battery_high": row.body_battery_high,
            "body_battery_low": row.body_battery_low,
            "weight_kg": row.weight_kg,
        }
        nutrition = repo.get_daily_nutrition(yesterday)
        if nutrition.get("entry_count", 0) > 0:
            metrics["nutrition"] = {
                **nutrition,
                "active_calories": row.active_calories,
                "resting_calories": row.resting_calories,
                "total_calories": row.total_calories,
            }

        try:
            _run_async(bot.send_daily_summary(metrics))
            logger.info("Daily report sent for %s", yesterday)
        except Exception as exc:
            logger.error("Failed to send daily report: %s", exc, exc_info=True)
            raise

    return report_callback
