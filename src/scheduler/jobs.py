"""Scheduler job definitions for manual sync and daily report."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, timedelta

from ..database.repository import Repository
from ..garmin.client import GarminClient
from ..nutrition.fatsecret_mapper import map_fatsecret_entries
from ..telegram.bot import TelegramBot, _row_to_metrics

logger = logging.getLogger(__name__)

# Heartbeat integration — optional: silently skipped if module not available
# (e.g. during local development without the HetznerCheck volume mounted).
try:
    from heartbeat import beat as _hb_beat  # mounted at /hetznercheck via PYTHONPATH
    _HEARTBEAT_AVAILABLE = True
except ImportError:
    _HEARTBEAT_AVAILABLE = False
    logger.debug("heartbeat module not available — liveness tracking disabled")


def _run_async(coro) -> None:
    """Run an async coroutine synchronously from a sync context."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(coro)
    finally:
        loop.close()


def make_sync_job(garmin: GarminClient, repo: Repository, fatsecret=None) -> callable:
    """Return a callable that syncs yesterday's Garmin data to the database.

    Args:
        garmin: Authenticated GarminClient.
        repo: Database repository.
        fatsecret: Optional FatSecretClient. When provided, yesterday's food
            diary is also fetched and upserted after the Garmin save. A failure
            in the FatSecret block is logged as a warning and never re-raised —
            Garmin data already committed must not be rolled back by a nutrition
            API failure.

    Returns:
        Callable used by /sync command.
    """
    def sync_yesterday_data_job() -> None:
        logger.info("Sync: starting Garmin sync")
        try:
            summary = garmin.get_yesterday_summary()
            metrics = garmin.to_metrics_dict(summary)
            repo.save_daily_metrics(summary.date, metrics)

            sync_status = "success" if metrics.get("garmin_sync_success") else "partial"
            repo.log_sync(sync_status)
            logger.info("Sync: complete for %s (status=%s)", summary.date, sync_status)

            if _HEARTBEAT_AVAILABLE:
                hb_status = "ok" if sync_status == "success" else "degraded"
                _hb_beat(
                    "GarminBot",
                    status=hb_status,
                    note=f"sync {sync_status} for {summary.date}",
                    next_in_seconds=86400,  # expect next run in ~24h
                )
        except Exception as exc:
            repo.log_sync("error", str(exc)[:500])
            logger.error("Sync: failed: %s", exc, exc_info=True)
            if _HEARTBEAT_AVAILABLE:
                _hb_beat(
                    "GarminBot",
                    status="error",
                    note=f"sync failed: {str(exc)[:120]}",
                    next_in_seconds=86400,
                )
            raise

        # FatSecret nutrition sync — separate try/except so a failure here
        # never affects the already-committed Garmin data or re-raises.
        if fatsecret is not None:
            try:
                raw = fatsecret.get_food_entries(summary.date)
                mapped = map_fatsecret_entries(raw)
                result = repo.upsert_fatsecret_entries(summary.date, mapped)
                logger.info(
                    "FatSecret: %d inserted, %d updated for %s",
                    result["inserted"],
                    result["updated"],
                    summary.date,
                )
            except Exception as exc:
                from ..nutrition.fatsecret_client import _redact
                logger.warning(
                    "FatSecret sync failed for %s (Garmin data unaffected): %s",
                    summary.date,
                    _redact(exc),
                )

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

        metrics = _row_to_metrics(row)
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


def make_newsletter_job(repo: Repository, bot: TelegramBot, groq_api_key: str) -> callable:
    """Return a callable that checks for new newsletter posts and generates daily insights.

    Args:
        repo: Database repository.
        bot: TelegramBot instance (for error notifications).
        groq_api_key: Groq API key for LLM analysis.

    Returns:
        Callable suitable for use as a scheduler job.
    """
    def newsletter_job() -> None:
        from ..newsletter.scraper import scrape_latest_post, scrape_post_content
        from ..newsletter.analyser import analyse_daily_post

        logger.info("Newsletter job: fetching latest post from listing page")
        post_meta = scrape_latest_post()
        if not post_meta:
            logger.error("Newsletter job: no posts found on listing page")
            raise RuntimeError("Nenhum artigo encontrado em arnoldspumpclub.com/blogs/newsletter")

        logger.info("Newsletter job: latest post is %r (%s)", post_meta.title, post_meta.url)

        # Skip if already stored and analysed today
        known_urls = {p.url for p in repo.get_all_newsletter_posts()}
        if post_meta.url in known_urls and repo.get_unsent_daily_insight() is None:
            # Already stored but insight was already sent — re-analyse for fresh delivery
            logger.info("Newsletter job: post already stored, re-analysing for /pump")

        try:
            content = scrape_post_content(post_meta.url)
        except Exception as exc:
            logger.error("Newsletter job: failed to scrape content: %s", exc)
            raise

        repo.save_newsletter_post(
            url=post_meta.url,
            title=post_meta.title,
            published_date=post_meta.published_date,
            content_text=content,
        )

        # Get yesterday's metrics for context
        yesterday = date.today() - timedelta(days=1)
        row = repo.get_metrics_by_date(yesterday)
        metrics = _row_to_metrics(row) if row else {}

        insight_text = analyse_daily_post(
            groq_api_key=groq_api_key,
            post_title=post_meta.title,
            post_content=content,
            yesterday_metrics=metrics,
        )

        repo.save_newsletter_insight(
            insight_pt=insight_text,
            insight_type="daily",
            post_url=post_meta.url,
            metrics_context=json.dumps(metrics, default=str),
        )
        logger.info("Newsletter job: insight saved for %r", post_meta.title)

    return newsletter_job


