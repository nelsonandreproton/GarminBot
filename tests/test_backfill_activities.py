"""Tests for scripts/backfill_activities.py — TDD, written before implementation.

Mirrors scripts/backfill_outsystems.py's test style: mock the Garmin client,
use a real temp-file Repository, test the backfill() function directly.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from unittest.mock import MagicMock

import pytest

from src.database.repository import Repository


@pytest.fixture
def repo():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    r = Repository(db_path)
    r.init_database()
    yield r
    r._engine.dispose()
    try:
        os.unlink(db_path)
    except PermissionError:
        pass


def _make_garmin_client(activities: list[dict]):
    client = MagicMock()
    client.get_activities_in_range.return_value = activities
    return client


def _activity(activity_id: int, day: date, min_hr: int | None = 90, **overrides) -> dict:
    base = dict(
        activity_id=activity_id, date=day, name="Walk", type_key="walking",
        duration_min=30, calories=200, distance_km=2.5,
        avg_hr=110, max_hr=140, min_hr=min_hr, is_indoor=False,
    )
    base.update(overrides)
    return base


class TestBackfillDryRun:
    def test_dry_run_makes_no_repo_writes(self, repo):
        from scripts.backfill_activities import backfill
        client = _make_garmin_client([_activity(1, date(2026, 1, 5))])

        backfill(repo, client, date(2026, 1, 1), date(2026, 1, 10), apply=False)

        assert repo.get_garmin_activities_range(date(2026, 1, 1), date(2026, 1, 10)) == []


class TestBackfillApply:
    def test_apply_writes_new_activity(self, repo):
        from scripts.backfill_activities import backfill
        client = _make_garmin_client([_activity(1, date(2026, 1, 5), min_hr=88)])

        backfill(repo, client, date(2026, 1, 1), date(2026, 1, 10), apply=True)

        rows = repo.get_garmin_activities_range(date(2026, 1, 1), date(2026, 1, 10))
        assert len(rows) == 1
        assert rows[0].min_hr == 88

    def test_skips_activity_already_having_min_hr(self, repo):
        from scripts.backfill_activities import backfill
        day = date(2026, 1, 5)
        repo.upsert_garmin_activity(
            1, day, "Walk", "walking", 30, 200, 2.5,
            avg_hr=110, max_hr=140, min_hr=88,
        )
        client = _make_garmin_client([_activity(1, day, min_hr=999)])

        backfill(repo, client, date(2026, 1, 1), date(2026, 1, 10), apply=True)

        rows = repo.get_garmin_activities_range(date(2026, 1, 1), date(2026, 1, 10))
        assert rows[0].min_hr == 88  # untouched — not overwritten with the "new" fetch value

    def test_backfills_activity_missing_min_hr(self, repo):
        """An existing row with min_hr=None should be updated (not skipped)."""
        from scripts.backfill_activities import backfill
        day = date(2026, 1, 5)
        repo.upsert_garmin_activity(
            1, day, "Walk", "walking", 30, 200, 2.5,
            avg_hr=110, max_hr=140, min_hr=None,
        )
        client = _make_garmin_client([_activity(1, day, min_hr=88)])

        backfill(repo, client, date(2026, 1, 1), date(2026, 1, 10), apply=True)

        rows = repo.get_garmin_activities_range(date(2026, 1, 1), date(2026, 1, 10))
        assert rows[0].min_hr == 88

    def test_does_not_call_range_fetch_detail_for_already_backfilled_activity(self, repo):
        """Already-backfilled activity_ids are passed as skip_detail_for so the
        client doesn't pay the per-activity detail-endpoint cost again on a re-run."""
        from scripts.backfill_activities import backfill
        day = date(2026, 1, 5)
        repo.upsert_garmin_activity(
            1, day, "Walk", "walking", 30, 200, 2.5,
            avg_hr=110, max_hr=140, min_hr=88,
        )
        client = _make_garmin_client([_activity(1, day, min_hr=999)])

        backfill(repo, client, date(2026, 1, 1), date(2026, 1, 10), apply=True)

        client.get_activities_in_range.assert_called_once_with(
            date(2026, 1, 1), date(2026, 1, 10), skip_detail_for={1}
        )

    def test_multiple_activities_mixed_skip_and_write(self, repo):
        from scripts.backfill_activities import backfill
        day1, day2 = date(2026, 1, 5), date(2026, 1, 6)
        repo.upsert_garmin_activity(
            1, day1, "Walk", "walking", 30, 200, 2.5,
            avg_hr=110, max_hr=140, min_hr=88,
        )
        client = _make_garmin_client([
            _activity(1, day1, min_hr=999),
            _activity(2, day2, min_hr=95),
        ])

        backfill(repo, client, date(2026, 1, 1), date(2026, 1, 10), apply=True)

        rows = repo.get_garmin_activities_range(date(2026, 1, 1), date(2026, 1, 10))
        by_id = {r.garmin_activity_id: r for r in rows}
        assert by_id[1].min_hr == 88
        assert by_id[2].min_hr == 95

    def test_activity_dates_preserved_across_range(self, repo):
        """Each activity must be stored under its own date, not flattened to one date."""
        from scripts.backfill_activities import backfill
        day1, day2 = date(2026, 1, 5), date(2026, 3, 20)
        client = _make_garmin_client([
            _activity(1, day1),
            _activity(2, day2),
        ])

        backfill(repo, client, date(2026, 1, 1), date(2026, 8, 20), apply=True)

        rows = repo.get_garmin_activities_range(date(2026, 1, 1), date(2026, 8, 20))
        dates = {r.garmin_activity_id: r.date for r in rows}
        assert dates[1] == day1
        assert dates[2] == day2


class TestBackfillFailureIsolation:
    def test_activity_with_no_parseable_date_is_skipped_not_crashed(self, repo):
        from scripts.backfill_activities import backfill
        client = _make_garmin_client([_activity(1, date(2026, 1, 5), date=None)])

        backfill(repo, client, date(2026, 1, 1), date(2026, 1, 10), apply=True)

        assert repo.get_garmin_activities_range(date(2026, 1, 1), date(2026, 1, 10)) == []

    def test_no_activities_found_does_not_crash(self, repo):
        from scripts.backfill_activities import backfill
        client = _make_garmin_client([])
        backfill(repo, client, date(2026, 1, 1), date(2026, 1, 10), apply=True)
        assert repo.get_garmin_activities_range(date(2026, 1, 1), date(2026, 1, 10)) == []

    def test_one_bad_activity_does_not_abort_the_rest(self, repo):
        """If get_activities_in_range itself only returns valid dicts, but repo write
        fails for one activity (e.g. bad data), the rest must still be processed.
        """
        from scripts.backfill_activities import backfill
        day1, day2 = date(2026, 1, 5), date(2026, 1, 6)
        client = _make_garmin_client([
            _activity(1, day1),
            _activity(2, day2),
        ])

        original_upsert = repo.upsert_garmin_activity
        def flaky_upsert(activity_id, *args, **kwargs):
            if activity_id == 1:
                raise RuntimeError("boom")
            return original_upsert(activity_id, *args, **kwargs)
        repo.upsert_garmin_activity = flaky_upsert

        backfill(repo, client, date(2026, 1, 1), date(2026, 1, 10), apply=True)

        repo.upsert_garmin_activity = original_upsert
        rows = repo.get_garmin_activities_range(date(2026, 1, 1), date(2026, 1, 10))
        assert len(rows) == 1
        assert rows[0].garmin_activity_id == 2
