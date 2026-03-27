"""Scheduler job definitions for manual sync and daily report."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date, timedelta

from ..database.repository import Repository
from ..garmin.client import GarminClient
from ..telegram.bot import TelegramBot, _row_to_metrics

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
        from ..newsletter.scraper import scrape_post_list, scrape_post_content
        from ..newsletter.analyser import analyse_daily_post

        latest_date = repo.get_latest_newsletter_post_date()
        if latest_date and latest_date >= (date.today() - timedelta(days=1)):
            logger.info("Newsletter job: latest post already up to date (%s), skipping scrape", latest_date)
            return

        logger.info("Newsletter job: checking for new posts")
        try:
            all_posts = scrape_post_list()
        except Exception as exc:
            logger.error("Newsletter job: failed to scrape post list: %s", exc)
            return

        # Load known URLs from DB to skip already-stored posts
        stored = repo.get_all_newsletter_posts()
        known_urls = {p.url for p in stored}
        new_posts = [p for p in reversed(all_posts) if p.url not in known_urls]

        if not new_posts:
            logger.info("Newsletter job: no new posts found")
            return

        # Process only the most recent new post (avoid flooding on first daily run)
        post_meta = new_posts[0]
        logger.info("Newsletter job: new post found — %r", post_meta.title)

        try:
            content = scrape_post_content(post_meta.url)
        except Exception as exc:
            logger.error("Newsletter job: failed to scrape content for %s: %s", post_meta.url, exc)
            return

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

        try:
            insight_text = analyse_daily_post(
                groq_api_key=groq_api_key,
                post_title=post_meta.title,
                post_content=content,
                yesterday_metrics=metrics,
            )
        except Exception as exc:
            logger.error("Newsletter job: LLM analysis failed: %s", exc)
            return

        repo.save_newsletter_insight(
            insight_pt=insight_text,
            insight_type="daily",
            post_url=post_meta.url,
            metrics_context=json.dumps(metrics, default=str),
        )
        logger.info("Newsletter job: insight saved for %r", post_meta.title)

    return newsletter_job


def run_newsletter_bulk_scrape(repo: Repository, bot: TelegramBot, groq_api_key: str) -> None:
    """Scrape all historical posts and send a one-time reference document via Telegram.

    Safe to call multiple times — exits early if posts already exist in the DB.
    Intended to be called once on startup.

    Args:
        repo: Database repository.
        bot: TelegramBot instance for sending the document.
        groq_api_key: Groq API key for LLM analysis.
    """
    if repo.get_all_newsletter_posts():
        logger.info("Newsletter bulk scrape: posts already present, skipping")
        return

    from ..newsletter.scraper import scrape_post_list, scrape_post_content
    from ..newsletter.analyser import analyse_historical_posts

    logger.info("Newsletter bulk scrape: starting initial scrape of all posts")
    try:
        all_posts = scrape_post_list()
    except Exception as exc:
        logger.error("Newsletter bulk scrape: failed to fetch post list: %s", exc)
        return

    if not all_posts:
        logger.warning("Newsletter bulk scrape: no posts found on site")
        return

    posts_data: list[dict] = []
    for i, post_meta in enumerate(all_posts, 1):
        logger.info(
            "Newsletter bulk scrape: scraping post %d/%d — %r",
            i, len(all_posts), post_meta.title,
        )
        try:
            content = scrape_post_content(post_meta.url)
            repo.save_newsletter_post(
                url=post_meta.url,
                title=post_meta.title,
                published_date=post_meta.published_date,
                content_text=content,
            )
            posts_data.append({"title": post_meta.title, "content": content})
        except Exception as exc:
            logger.warning("Newsletter bulk scrape: failed for %s: %s", post_meta.url, exc)
        time.sleep(2.5)

    if not posts_data:
        logger.error("Newsletter bulk scrape: no posts successfully scraped")
        return

    logger.info("Newsletter bulk scrape: analysing %d posts with LLM", len(posts_data))
    try:
        historical_doc = analyse_historical_posts(
            groq_api_key=groq_api_key,
            posts=posts_data,
        )
    except Exception as exc:
        logger.error("Newsletter bulk scrape: historical analysis failed: %s", exc)
        return

    # Store the historical insight
    repo.save_newsletter_insight(
        insight_pt=historical_doc,
        insight_type="historical",
        post_url=None,
    )

    logger.info("Newsletter bulk scrape: complete (%d posts processed)", len(posts_data))
