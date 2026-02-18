"""APScheduler job definitions for daily sync, daily report, weekly report, wake detection, and backup."""

from __future__ import annotations

import asyncio
import logging
import time as _time
from datetime import date, timedelta

from ..database.repository import Repository
from ..garmin.client import GarminClient
from ..telegram.bot import TelegramBot
from ..utils.backup import create_backup
from ..utils.charts import generate_weekly_chart
from ..utils.insights import generate_insights

logger = logging.getLogger(__name__)


def _run_async(coro) -> None:
    """Run an async coroutine synchronously from a sync scheduler job."""
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
        Callable suitable for APScheduler.
    """
    def sync_yesterday_data_job() -> None:
        logger.info("Job: starting daily Garmin sync")
        try:
            summary = garmin.get_yesterday_summary()
            metrics = garmin.to_metrics_dict(summary)
            repo.save_daily_metrics(summary.date, metrics)

            status = "success" if metrics.get("garmin_sync_success") else "partial"
            repo.log_sync(status)
            logger.info("Job: sync complete for %s (status=%s)", summary.date, status)
        except Exception as exc:
            repo.log_sync("error", str(exc))
            logger.error("Job: sync failed: %s", exc, exc_info=True)
            raise  # Let APScheduler's error listener handle it

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
        _send_daily_report(repo, bot)

    return report_callback


def make_weekly_report_job(repo: Repository, bot: TelegramBot, database_path: str) -> callable:
    """Return a callable that sends the weekly report, chart, insights, and creates a backup.

    Args:
        repo: Database repository.
        bot: TelegramBot instance.
        database_path: Path to the SQLite database (for weekly backup).

    Returns:
        Callable suitable for APScheduler.
    """
    def send_weekly_report_job() -> None:
        logger.info("Job: sending weekly report")
        yesterday = date.today() - timedelta(days=1)
        stats = repo.get_weekly_stats(yesterday)

        if not stats:
            logger.warning("Job: no weekly data available")
            _run_async(bot.send_error(
                "relatório semanal",
                RuntimeError("Sem dados suficientes para o relatório semanal."),
            ))
            return

        try:
            weight_stats = repo.get_weekly_weight_stats(yesterday)
            _run_async(bot.send_weekly_report(stats, weight_stats=weight_stats or None))

            # Chart
            start = stats.get("start_date", yesterday - timedelta(days=6))
            rows = repo.get_metrics_range(start, yesterday)
            if rows:
                goals = repo.get_goals()
                chart_bytes = generate_weekly_chart(rows, goals=goals)
                if chart_bytes:
                    _run_async(bot.send_image(chart_bytes, caption="📊 Evolução semanal"))

                # Insights (14 days for trend detection)
                all_rows = repo.get_metrics_range(yesterday - timedelta(days=13), yesterday)
                insights = generate_insights(all_rows, goals=goals)
                if insights:
                    insight_text = "💡 *Insights:*\n" + "\n".join(f"• {i}" for i in insights)
                    _run_async(bot._send(insight_text))

            logger.info("Job: weekly report sent")
        except Exception as exc:
            logger.error("Job: failed to send weekly report: %s", exc, exc_info=True)
            raise
        finally:
            # Always back up regardless of send outcome
            create_backup(database_path)

    return send_weekly_report_job


def make_sync_retry_job(garmin: GarminClient, repo: Repository) -> callable:
    """Return a job that retries today's sync only if it previously failed.

    Args:
        garmin: Authenticated GarminClient.
        repo: Database repository.

    Returns:
        Callable suitable for APScheduler.
    """
    def sync_retry_job() -> None:
        if repo.has_successful_sync_today():
            logger.info("Job: retry-sync skipped — already synced successfully today")
            return
        logger.info("Job: running retry sync")
        make_sync_job(garmin, repo)()

    return sync_retry_job


def make_wake_check_job(
    garmin: GarminClient,
    repo: Repository,
) -> callable:
    """Return a job that polls Garmin for completed sleep data to detect wake-up.

    When sleep data becomes available for today, it means the user has woken up
    and their device has synced. Syncs data and marks the day as done so the
    fallback job does not double-sync.

    This job is designed to run periodically (e.g. every 10 minutes) within a
    configured morning window. It is a no-op if a sync was already done today.

    Args:
        garmin: Authenticated GarminClient.
        repo: Database repository.

    Returns:
        Callable suitable for APScheduler.
    """
    def wake_check_job() -> None:
        # Skip if report was already sent today
        if repo.has_report_sent_today():
            logger.debug("Wake check: report already sent today, skipping")
            return

        today = date.today()
        logger.info("Wake check: polling Garmin for sleep data (date=%s)", today)

        if not garmin.check_sleep_available(today):
            logger.info("Wake check: no sleep data yet — still sleeping or device not synced")
            return

        logger.info("Wake check: sleep data detected — user has woken up!")

        # Run the full sync; report is sent manually via /sync
        try:
            summary = garmin.get_yesterday_summary()
            metrics = garmin.to_metrics_dict(summary)
            repo.save_daily_metrics(summary.date, metrics)
            status = "success" if metrics.get("garmin_sync_success") else "partial"
            repo.log_sync(status)
            repo.log_report_sent()  # Mark as done so fallback doesn't double-sync
            logger.info("Wake check: sync complete for %s (status=%s)", summary.date, status)
        except Exception as exc:
            repo.log_sync("error", str(exc))
            logger.error("Wake check: sync failed: %s", exc, exc_info=True)

    return wake_check_job


def make_wake_fallback_job(
    garmin: GarminClient,
    repo: Repository,
) -> callable:
    """Return a fallback job that runs at the end of the wake detection window.

    If wake detection hasn't triggered by the end of the window (e.g. user
    didn't wear the watch, or device didn't sync), force-syncs with whatever
    data is available. No automatic report is sent.

    Args:
        garmin: Authenticated GarminClient.
        repo: Database repository.

    Returns:
        Callable suitable for APScheduler.
    """
    def wake_fallback_job() -> None:
        if repo.has_report_sent_today():
            logger.info("Wake fallback: report already sent today, skipping")
            return

        logger.info("Wake fallback: end of detection window, forcing sync (no auto-report)")

        # Attempt sync even if data is incomplete; report is sent manually via /sync
        try:
            summary = garmin.get_yesterday_summary()
            metrics = garmin.to_metrics_dict(summary)
            repo.save_daily_metrics(summary.date, metrics)
            status = "success" if metrics.get("garmin_sync_success") else "partial"
            repo.log_sync(status)
            logger.info("Wake fallback: sync complete for %s (status=%s)", summary.date, status)
        except Exception as exc:
            repo.log_sync("error", str(exc))
            logger.error("Wake fallback: sync failed: %s", exc, exc_info=True)

    return wake_fallback_job


def _send_daily_report(repo: Repository, bot: TelegramBot) -> None:
    """Shared helper: send the daily report and mark it as sent."""
    yesterday = date.today() - timedelta(days=1)
    row = repo.get_metrics_by_date(yesterday)

    if row is None:
        logger.warning("Daily report: no data for %s, alerting user", yesterday)
        _run_async(bot.send_error(
            f"relatório de {yesterday}",
            RuntimeError("Sem dados para ontem — o sync falhou ou ainda não correu."),
        ))
        repo.log_report_sent()  # Mark sent to avoid duplicate error messages
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
    # Attach daily nutrition totals if any food was logged
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
        repo.log_report_sent()
        logger.info("Daily report sent for %s", yesterday)
    except Exception as exc:
        logger.error("Failed to send daily report: %s", exc, exc_info=True)
        raise
