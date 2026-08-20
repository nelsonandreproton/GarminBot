"""One-time backfill: populate garmin_activities with min_hr (and all other
fields) for every activity since a given start date.

The activity-list endpoint (used by the daily /sync) does not return minHR —
only the per-activity detail endpoint does. This script re-fetches min_hr for
every activity in range via the detail endpoint (one extra API call per
activity), skipping activities whose row already has min_hr populated so a
re-run only does work for the gap.

Usage:
    python -m scripts.backfill_activities --start 2026-01-01 [--apply]

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


def backfill(repo: Repository, garmin: GarminClient, start: date, end: date, apply: bool) -> None:
    # Pacing between per-activity detail calls (min_hr, strength sets) happens
    # inside get_activities_in_range itself, since that's where those API
    # calls are actually made — a sleep here would only pace DB writes.
    existing_by_id = {
        row.garmin_activity_id: row for row in repo.get_garmin_activities_range(start, end)
    }
    already_backfilled = {
        activity_id for activity_id, row in existing_by_id.items() if row.min_hr is not None
    }

    # Skipping the detail call for already-backfilled activities makes a
    # re-run (e.g. to pick up newly-synced days) cheap — it only pays the
    # per-activity API cost for what's actually missing, not all 60+ again.
    activities = garmin.get_activities_in_range(start, end, skip_detail_for=already_backfilled)
    logger.info("Found %d activities between %s and %s", len(activities), start, end)

    created_or_updated, skipped, failed = 0, 0, 0

    for act in activities:
        activity_id = act["activity_id"]
        existing = existing_by_id.get(activity_id)
        if existing is not None and existing.min_hr is not None:
            logger.info("SKIP %s — already backfilled (min_hr=%s)", activity_id, existing.min_hr)
            skipped += 1
            continue

        # Prefer the already-known date for rows the daily sync already created
        # — it's authoritative and needs no parsing of startTimeLocal.
        day = existing.date if existing is not None else act.get("date")
        if day is None:
            logger.warning("FAIL activity %s — no parseable date, skipping", activity_id)
            failed += 1
            continue

        if not apply:
            logger.info("WOULD WRITE activity %s on %s (min_hr=%s)", activity_id, day, act.get("min_hr"))
            created_or_updated += 1
            continue

        try:
            repo.upsert_garmin_activity(
                activity_id=activity_id,
                day=day,
                name=act["name"],
                type_key=act.get("type_key"),
                duration_min=act.get("duration_min"),
                calories=act.get("calories"),
                distance_km=act.get("distance_km"),
                avg_hr=act.get("avg_hr"),
                max_hr=act.get("max_hr"),
                min_hr=act.get("min_hr"),
                is_indoor=act.get("is_indoor"),
                total_sets=act.get("total_sets"),
                total_reps=act.get("total_reps"),
                min_weight_kg=act.get("min_weight_kg"),
                max_weight_kg=act.get("max_weight_kg"),
            )
            logger.info("WROTE activity %s (min_hr=%s)", activity_id, act.get("min_hr"))
            created_or_updated += 1
        except Exception as exc:
            logger.error("FAIL activity %s — upsert failed: %s", activity_id, exc)
            failed += 1

    verb = "Would write" if not apply else "Wrote"
    logger.info("\nDone. %s: %d, Skipped: %d, Failed: %d", verb, created_or_updated, skipped, failed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date, YYYY-MM-DD (default: today)")
    parser.add_argument("--apply", action="store_true", help="Actually write records (default: dry run)")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()

    config = load_config()
    repo = Repository(config.database_path)
    repo.init_database()
    garmin = GarminClient(config.garmin_email, config.garmin_password)

    mode = "APPLY (will write to DB)" if args.apply else "DRY RUN (no writes)"
    logger.info("Backfilling activities %s to %s — mode: %s\n", start, end, mode)
    backfill(repo, garmin, start, end, args.apply)


if __name__ == "__main__":
    main()
