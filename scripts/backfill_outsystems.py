"""One-time backfill: create OutSystems daily records for past days that are
missing one. CreateRecord has no update endpoint, so a day is skipped (not
created) whenever existence can't be verified or nutrition data is missing
(would be false-zero macros). Unlike the daily /sync job, a missing weigh-in
does NOT block backfill — weight is sent as 0 for days with no real weigh-in
(Nelson doesn't weigh in daily; historical backfill still has value without it).

Usage:
    python -m scripts.backfill_outsystems --start 2026-01-01 [--apply]

Without --apply, runs as a dry run: prints what would be created/skipped and
writes nothing to OutSystems.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime

from src.config import load_config
from src.database.repository import Repository
from src.integrations.outsystems_client import OutSystemsClient

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def backfill(repo: Repository, outsystems: OutSystemsClient, start: date, end: date, apply: bool) -> None:
    rows = repo.get_metrics_range(start, end)
    created, skipped, failed = 0, 0, 0

    for row in rows:
        day = row.date
        active = row.active_calories
        resting = row.resting_calories
        weight = row.weight_kg
        steps = row.steps

        if None in (active, resting, steps):
            logger.info("SKIP %s — missing Garmin field(s) (active=%s, resting=%s, steps=%s)",
                        day, active, resting, steps)
            skipped += 1
            continue

        if weight is None:
            logger.info("%s — no weigh-in that day, sending weight=0", day)
            weight = 0.0

        nutrition = repo.get_daily_nutrition(day)
        if nutrition.get("entry_count", 0) == 0:
            logger.info("SKIP %s — no FatSecret nutrition entries (would be false-zero macros)", day)
            skipped += 1
            continue

        try:
            if outsystems.record_exists(day):
                logger.info("SKIP %s — OutSystems record already exists", day)
                skipped += 1
                continue
        except Exception as exc:
            logger.warning("FAIL %s — could not verify existence: %s", day, exc)
            failed += 1
            continue

        food = nutrition["calories"]
        protein = nutrition["protein_g"]
        carbs = nutrition["carbs_g"]
        fat = nutrition["fat_g"]

        if not apply:
            logger.info(
                "WOULD CREATE %s — active=%.0f rest=%.0f food=%.0f protein=%.0f carbs=%.0f fat=%.0f weight=%.1f steps=%.0f",
                day, active, resting, food, protein, carbs, fat, weight, steps,
            )
            created += 1
            continue

        try:
            outsystems.create_record(
                day=day, active=active, rest=resting, food=food,
                protein=protein, carbs=carbs, fat=fat, weight=weight, steps=steps,
            )
            logger.info("CREATED %s", day)
            created += 1
        except Exception as exc:
            logger.error("FAIL %s — create_record failed: %s", day, exc)
            failed += 1

    verb = "Would create" if not apply else "Created"
    logger.info("\nDone. %s: %d, Skipped: %d, Failed: %d", verb, created, skipped, failed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date, YYYY-MM-DD (default: today)")
    parser.add_argument("--apply", action="store_true", help="Actually create records (default: dry run)")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()

    config = load_config()
    if not config.outsystems_api_base_url:
        raise SystemExit("OUTSYSTEMS_API_BASE_URL not configured — aborting.")

    repo = Repository(config.database_path)
    outsystems = OutSystemsClient(config.outsystems_api_base_url)

    mode = "APPLY (will write to OutSystems)" if args.apply else "DRY RUN (no writes)"
    logger.info("Backfilling %s to %s — mode: %s\n", start, end, mode)
    backfill(repo, outsystems, start, end, args.apply)


if __name__ == "__main__":
    main()
