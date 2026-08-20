"""One-time backfill: fix body_battery_high/low for days already synced with
the "68-68" bug (max/min taken from a 1-item list of the day's cumulative
"charged" total, instead of the actual level trend in bodyBatteryValuesArray).

Only days on/after 2026-04-01 are recoverable — Garmin's API returns null
level readings for every point before that date (confirmed empirically: the
array has entries but every value is None), so January-March stay wrong.

Usage:
    python -m scripts.backfill_body_battery --start 2026-04-01 [--apply]

Without --apply, runs as a dry run: logs what would be written and writes
nothing to the database.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime

from src.config import load_config
from src.database.repository import Repository
from src.garmin.client import GarminClient

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

EARLIEST_RECOVERABLE = date(2026, 4, 1)


def _fetch_level_range(client, day: date) -> tuple[int, int] | None:
    garmin = client._ensure_authenticated()
    bb = garmin.get_body_battery(day.isoformat())
    if not bb:
        return None
    points = bb[0].get("bodyBatteryValuesArray") or []
    levels = [p[1] for p in points if len(p) > 1 and p[1] is not None]
    if not levels:
        return None
    return min(levels), max(levels)


def backfill(repo: Repository, client: GarminClient, start: date, end: date, apply: bool) -> None:
    if start < EARLIEST_RECOVERABLE:
        logger.info(
            "Start date %s is before %s — Garmin has no recoverable level data "
            "before that date, clamping start.", start, EARLIEST_RECOVERABLE,
        )
        start = EARLIEST_RECOVERABLE

    rows = repo.get_metrics_range(start, end)
    fixed, skipped, unrecoverable, failed = 0, 0, 0, 0

    for row in rows:
        day = row.date
        if row.body_battery_high is None or row.body_battery_high != row.body_battery_low:
            logger.info("SKIP %s — not the bug pattern (high=%s low=%s)",
                        day, row.body_battery_high, row.body_battery_low)
            skipped += 1
            continue

        try:
            levels = _fetch_level_range(client, day)
        except Exception as exc:
            logger.error("FAIL %s — could not fetch body battery: %s", day, exc)
            failed += 1
            continue

        if levels is None:
            logger.info("UNRECOVERABLE %s — no level data available from Garmin", day)
            unrecoverable += 1
            continue

        low, high = levels
        if not apply:
            logger.info("WOULD FIX %s — %s-%s -> %s-%s", day, row.body_battery_low,
                         row.body_battery_high, low, high)
            fixed += 1
            continue

        repo.save_daily_metrics(day, {"body_battery_high": high, "body_battery_low": low})
        logger.info("FIXED %s — %s-%s", day, low, high)
        fixed += 1

    verb = "Would fix" if not apply else "Fixed"
    logger.info(
        "\nDone. %s: %d, Skipped (not buggy): %d, Unrecoverable: %d, Failed: %d",
        verb, fixed, skipped, unrecoverable, failed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=EARLIEST_RECOVERABLE.isoformat(),
                         help="Start date, YYYY-MM-DD (default: earliest recoverable, 2026-04-01)")
    parser.add_argument("--end", default=None, help="End date, YYYY-MM-DD (default: today)")
    parser.add_argument("--apply", action="store_true", help="Actually write fixes (default: dry run)")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()

    config = load_config()
    repo = Repository(config.database_path)
    repo.init_database()
    garmin = GarminClient(config.garmin_email, config.garmin_password)

    mode = "APPLY (will write to DB)" if args.apply else "DRY RUN (no writes)"
    logger.info("Backfilling body battery %s to %s — mode: %s\n", start, end, mode)
    backfill(repo, garmin, start, end, args.apply)


if __name__ == "__main__":
    main()
