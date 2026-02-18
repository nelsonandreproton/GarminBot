"""Entry point: initialise all components, run health checks and bot."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import date, timedelta

from .config import ConfigError, load_config
from .database.repository import Repository
from .garmin.client import GarminClient
from .scheduler.jobs import make_report_callback, make_sync_job
from .telegram.bot import TelegramBot
from .utils.logger import setup_logging

logger = logging.getLogger(__name__)


def _run_health_checks(garmin: GarminClient, repo: Repository, bot_token: str, chat_id: int) -> None:
    """Verify connectivity to Garmin, Telegram, and the database on startup."""
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
    tg_bot._garmin_report = make_report_callback(repo, tg_bot)

    # Health checks (non-fatal for Garmin/Telegram)
    _run_health_checks(garmin, repo, config.telegram_bot_token, int(config.telegram_chat_id))

    # Startup backfill: fill any gaps in the last 7 days
    _run_startup_backfill(garmin, repo)

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
                "uptime_seconds": int(_time.monotonic() - _startup_time),
            }

        start_health_server(config.health_port, _get_health_status)

    # Build Telegram application
    app = tg_bot.build_application()

    # Register commands with BotFather
    asyncio.get_event_loop().run_until_complete(tg_bot.register_commands())

    # Graceful shutdown handler
    def _shutdown(signum, frame):
        logger.info("Shutdown signal received, stopping...")
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
        logger.info("GarminBot stopped")


if __name__ == "__main__":
    run()
