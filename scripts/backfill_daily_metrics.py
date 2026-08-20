"""One-time backfill: fill days with no daily_metrics row at all since a
given start date. Unlike the Telegram /backfill command (capped at 30 days
per call), this covers an arbitrary range in one run.

Does NOT re-fetch activities — scripts/backfill_activities.py already covers
the full activity history for any date range via one cheap list call, so
re-fetching per missing day here would only duplicate work and roughly double
the API call count for zero new data.

get_summary_for_date alone issues ~9-10 raw Garmin API calls per day (sleep
x1-2, stats x2 — once for steps/calories, once for resting HR — stress, body
battery, SpO2, intensity minutes, weight x1-2). For 100+ days that is close
to 1000 calls in one run against an API where a 429 is an IP ban, not just a
per-request error — run a narrow slice first (e.g. --start/--end a few days)
before committing to the full range.

Usage:
    python -m scripts.backfill_daily_metrics --start 2026-01-01 [--apply]
    python -m scripts.backfill_daily_metrics --start 2026-01-01 --end 2026-01-10 --apply

Without --apply, runs as a dry run: logs which days would be filled and
writes nothing to the database (no Garmin calls are made either).
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta

from src.config import load_config
from src.database.repository import Repository
from src.garmin.client import GarminClient, _is_rate_limit

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def backfill(repo: Repository, garmin: GarminClient, start: date, end: date, apply: bool) -> None:
    missing = repo.get_missing_dates(start, end)
    logger.info("Found %d missing day(s) between %s and %s", len(missing), start, end)

    filled, failed = 0, 0
    for day in missing:
        if not apply:
            logger.info("WOULD FILL %s", day)
            filled += 1
            continue

        try:
            summary = garmin.get_summary_for_date(day)
            metrics = garmin.to_metrics_dict(summary)
            repo.save_daily_metrics(day, metrics)
            repo.log_sync("success" if metrics.get("garmin_sync_success") else "partial")
            logger.info("FILLED %s", day)
            filled += 1
        except Exception as exc:
            if _is_rate_limit(exc):
                logger.warning("Garmin 429 on %s — stopping to avoid extending the ban", day)
                break
            repo.log_sync("error", str(exc)[:500])
            logger.error("FAIL %s — could not fetch daily metrics: %s", day, exc)
            failed += 1
        finally:
            time.sleep(2)

    verb = "Would fill" if not apply else "Filled"
    logger.info("\nDone. %s: %d, Failed: %d, Total missing: %d", verb, filled, failed, len(missing))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date, YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--apply", action="store_true", help="Actually write to the DB (default: dry run)")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today() - timedelta(days=1)

    config = load_config()
    repo = Repository(config.database_path)
    repo.init_database()
    garmin = GarminClient(config.garmin_email, config.garmin_password)

    mode = "APPLY (will write to DB)" if args.apply else "DRY RUN (no writes)"
    logger.info("Backfilling daily metrics %s to %s — mode: %s\n", start, end, mode)
    backfill(repo, garmin, start, end, args.apply)


if __name__ == "__main__":
    main()
