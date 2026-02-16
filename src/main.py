"""Entry point: initialise all components, run health checks, start scheduler and bot."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import date, timedelta

from apscheduler.events import EVENT_JOB_ERROR, JobExecutionEvent
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone as pytz_timezone

from .config import ConfigError, load_config
from .database.repository import Repository
from .garmin.client import GarminClient
from .scheduler.jobs import (
    make_daily_report_job,
    make_sync_job,
    make_sync_retry_job,
    make_wake_check_job,
    make_wake_fallback_job,
    make_weekly_report_job,
)
from .telegram.bot import TelegramBot
from .utils.logger import setup_logging

logger = logging.getLogger(__name__)

_WEEKDAY_MAP = {
    "monday": "mon", "tuesday": "tue", "wednesday": "wed",
    "thursday": "thu", "friday": "fri", "saturday": "sat", "sunday": "sun",
}


def _job_error_listener(event: JobExecutionEvent) -> None:
    logger.error(
        "Scheduler job '%s' raised an exception: %s",
        event.job_id,
        event.exception,
        exc_info=(type(event.exception), event.exception, event.exception.__traceback__),
    )


def _run_health_checks(garmin: GarminClient, repo: Repository, bot_token: str, chat_id: int) -> None:
    """Verify connectivity to Garmin, Telegram, and the database on startup."""
    import sqlite3
    from pathlib import Path

    # Database
    try:
        count = repo.count_stored_days()
        logger.info("Health: database OK (%d days stored)", count)
    except Exception as exc:
        logger.error("Health: database check failed: %s", exc)
        raise

    # Garmin
    try:
        garmin.authenticate()
        logger.info("Health: Garmin authentication OK")
    except Exception as exc:
        logger.warning("Health: Garmin authentication failed: %s — will retry on first sync", exc)

    # Telegram (send a startup ping via the async bot)
    try:
        from telegram import Bot
        async def _ping():
            bot = Bot(token=bot_token)
            me = await bot.get_me()
            logger.info("Health: Telegram OK (@%s)", me.username)
        asyncio.get_event_loop().run_until_complete(_ping())
    except Exception as exc:
        logger.warning("Health: Telegram check failed: %s", exc)


def _run_startup_backfill(garmin: GarminClient, repo: Repository) -> None:
    """Fill gaps in the last 7 days silently on startup."""
    import time
    yesterday = date.today() - timedelta(days=1)
    start = yesterday - timedelta(days=6)
    missing = repo.get_missing_dates(start, yesterday)
    if not missing:
        return
    logger.info("Startup backfill: %d missing days found (%s to %s)", len(missing), missing[0], missing[-1])
    for day in missing:
        try:
            summary = garmin.get_summary_for_date(day)
            metrics = garmin.to_metrics_dict(summary)
            repo.save_daily_metrics(day, metrics)
            repo.log_sync("success")
            logger.info("Startup backfill: filled %s", day)
            time.sleep(2)  # rate limiting
        except Exception as exc:
            logger.warning("Startup backfill: failed for %s: %s", day, exc)


def _build_scheduler(config, repo: Repository, garmin: GarminClient, tg_bot: TelegramBot) -> BackgroundScheduler:
    tz = pytz_timezone(config.timezone)
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_listener(_job_error_listener, EVENT_JOB_ERROR)

    weekly_job = make_weekly_report_job(repo, tg_bot, config.database_path)

    if config.wake_detection:
        # Wake detection mode: poll Garmin for sleep data to detect wake-up,
        # then sync + send report. No fixed sync/report times.
        _build_wake_detection_jobs(scheduler, config, repo, garmin, tg_bot, tz)
    else:
        # Fixed-time mode: sync and report at configured times (original behaviour)
        _build_fixed_time_jobs(scheduler, config, repo, garmin, tg_bot, tz)

    weekly_day = _WEEKDAY_MAP.get(config.weekly_report_day.lower(), "sun")
    scheduler.add_job(
        weekly_job,
        CronTrigger(day_of_week=weekly_day, hour=config.weekly_hour, minute=config.weekly_minute, timezone=tz),
        id="weekly_report",
        name="Weekly Report",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=7200,
    )

    return scheduler


def _build_fixed_time_jobs(
    scheduler: BackgroundScheduler,
    config,
    repo: Repository,
    garmin: GarminClient,
    tg_bot: TelegramBot,
    tz,
) -> None:
    """Add the original fixed-time sync, report, and retry jobs."""
    sync_job = make_sync_job(garmin, repo)
    report_job = make_daily_report_job(repo, tg_bot, config)

    scheduler.add_job(
        sync_job,
        CronTrigger(hour=config.sync_hour, minute=config.sync_minute, timezone=tz),
        id="daily_sync",
        name="Daily Garmin Sync",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        report_job,
        CronTrigger(hour=config.report_hour, minute=config.report_minute, timezone=tz),
        id="daily_report",
        name="Daily Report",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    retry_job = make_sync_retry_job(garmin, repo)
    retry_total_minutes = config.sync_hour * 60 + config.sync_minute + config.sync_retry_delay_minutes
    retry_hour = (retry_total_minutes // 60) % 24
    retry_minute = retry_total_minutes % 60
    scheduler.add_job(
        retry_job,
        CronTrigger(hour=retry_hour, minute=retry_minute, timezone=tz),
        id="daily_sync_retry",
        name="Daily Sync Retry",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )


def _build_wake_detection_jobs(
    scheduler: BackgroundScheduler,
    config,
    repo: Repository,
    garmin: GarminClient,
    tg_bot: TelegramBot,
    tz,
) -> None:
    """Add wake detection polling job and end-of-window fallback.

    Instead of syncing and reporting at fixed times, poll Garmin every N minutes
    for completed sleep data. When found, it means the user woke up and the
    device synced — trigger the full sync + daily report at that moment.

    A fallback job at WAKE_CHECK_END ensures the report is still sent even if
    the device never synced (e.g. watch not worn).
    """
    wake_job = make_wake_check_job(garmin, repo, tg_bot, config)
    fallback_job = make_wake_fallback_job(garmin, repo, tg_bot, config)

    # Build hour range for the cron expression: e.g. "5-11" for 05:00-11:59
    # The fallback job at WAKE_CHECK_END covers the final hour boundary.
    start_h = config.wake_start_hour
    end_h = config.wake_end_hour - 1 if config.wake_end_minute == 0 else config.wake_end_hour

    # Ensure valid range (start might equal end if window is ~1 hour)
    if end_h < start_h:
        end_h = start_h

    interval = config.wake_check_interval_minutes
    minute_expr = f"*/{interval}" if interval < 60 else "0"

    scheduler.add_job(
        wake_job,
        CronTrigger(hour=f"{start_h}-{end_h}", minute=minute_expr, timezone=tz),
        id="wake_check",
        name="Wake Detection Check",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )

    scheduler.add_job(
        fallback_job,
        CronTrigger(hour=config.wake_end_hour, minute=config.wake_end_minute, timezone=tz),
        id="wake_fallback",
        name="Wake Detection Fallback",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )


def run() -> None:
    """Main application entry point."""
    # Minimal early logging before config is loaded
    logging.basicConfig(level=logging.INFO)

    try:
        config = load_config()
    except ConfigError as exc:
        logging.critical("Configuration error: %s", exc)
        sys.exit(1)

    setup_logging(config.log_level, config.log_file)
    logger.info("GarminBot starting up")

    # Initialise components
    repo = Repository(config.database_path)
    repo.init_database()

    garmin = GarminClient(config.garmin_email, config.garmin_password)

    def sync_callback() -> None:
        """Used by /sync command to trigger a manual sync."""
        make_sync_job(garmin, repo)()

    def backfill_callback(missing_dates: list) -> None:
        import time
        for day in missing_dates:
            try:
                summary = garmin.get_summary_for_date(day)
                metrics = garmin.to_metrics_dict(summary)
                repo.save_daily_metrics(day, metrics)
                repo.log_sync("success")
                time.sleep(2)
            except Exception as exc:
                repo.log_sync("error", str(exc))
                logger.error("Backfill failed for %s: %s", day, exc)

    tg_bot = TelegramBot(config, repo, garmin_sync_callback=sync_callback, garmin_backfill_callback=backfill_callback, garmin_client=garmin)

    # Health checks (non-fatal for Garmin/Telegram)
    _run_health_checks(garmin, repo, config.telegram_bot_token, int(config.telegram_chat_id))

    # Startup backfill: fill any gaps in the last 7 days
    _run_startup_backfill(garmin, repo)

    # Build and start scheduler
    scheduler = _build_scheduler(config, repo, garmin, tg_bot)
    scheduler.start()
    if config.wake_detection:
        logger.info(
            "Scheduler started (wake detection mode). Checking every %dmin from %s to %s, "
            "Weekly on %s at %02d:%02d (%s)",
            config.wake_check_interval_minutes,
            config.wake_check_start, config.wake_check_end,
            config.weekly_report_day, config.weekly_hour, config.weekly_minute,
            config.timezone,
        )
    else:
        logger.info(
            "Scheduler started (fixed-time mode). Sync at %02d:%02d, Report at %02d:%02d, "
            "Weekly on %s at %02d:%02d (%s)",
            config.sync_hour, config.sync_minute,
            config.report_hour, config.report_minute,
            config.weekly_report_day, config.weekly_hour, config.weekly_minute,
            config.timezone,
        )

    # Start health check server if configured
    if config.health_port:
        from .utils.healthcheck import start_health_server
        import time as _time
        _startup_time = _time.monotonic()

        def _get_health_status():
            last = repo.get_last_successful_sync()
            last_sync_dt = last.sync_date if last else None
            from datetime import UTC, datetime
            if last_sync_dt:
                age_hours = (datetime.now(UTC) - last_sync_dt.replace(tzinfo=UTC)).total_seconds() / 3600
                ok = age_hours < 48
            else:
                ok = False
            return {
                "status": "ok" if ok else "degraded",
                "ok": ok,
                "last_sync": str(last_sync_dt) if last_sync_dt else None,
                "scheduler_running": scheduler.running,
                "uptime_seconds": int(_time.monotonic() - _startup_time),
            }

        start_health_server(config.health_port, _get_health_status)

    # Build Telegram application
    app = tg_bot.build_application()
    app.bot_data["scheduler"] = scheduler

    # Register commands with BotFather
    asyncio.get_event_loop().run_until_complete(tg_bot.register_commands())

    # Graceful shutdown handler
    def _shutdown(signum, frame):
        logger.info("Shutdown signal received, stopping...")
        scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped cleanly")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info("GarminBot running. Press Ctrl+C to stop.")

    # Run telegram bot in polling mode (blocking)
    try:
        app.run_polling(allowed_updates=["message", "callback_query"])
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown(wait=True)
        logger.info("GarminBot stopped")


if __name__ == "__main__":
    run()
