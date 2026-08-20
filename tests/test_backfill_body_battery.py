"""Tests for scripts/backfill_body_battery.py.

Mirrors scripts/backfill_activities.py's test style: mock the Garmin client's
underlying garminconnect client, use a real temp-file Repository, test the
backfill() function directly.
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


def _make_garmin_client(body_battery_by_date: dict[str, list]):
    client = MagicMock()
    underlying = MagicMock()
    underlying.get_body_battery.side_effect = lambda d: body_battery_by_date.get(d, [])
    client._ensure_authenticated.return_value = underlying
    return client


def _bb_response(low: int, high: int) -> list:
    return [{
        "date": "irrelevant",
        "charged": 66, "drained": 77,
        "bodyBatteryValuesArray": [[1, low], [2, high], [3, (low + high) // 2]],
    }]


def _bb_no_levels() -> list:
    return [{"date": "irrelevant", "charged": 31, "drained": 27,
              "bodyBatteryValuesArray": [[1, None], [2, None]]}]


class TestBackfillDryRun:
    def test_dry_run_makes_no_repo_writes(self, repo):
        from scripts.backfill_body_battery import backfill
        day = date(2026, 4, 5)
        repo.save_daily_metrics(day, {"body_battery_high": 66, "body_battery_low": 66})
        client = _make_garmin_client({day.isoformat(): _bb_response(10, 87)})

        backfill(repo, client, date(2026, 4, 1), date(2026, 4, 10), apply=False)

        row = repo.get_metrics_by_date(day)
        assert row.body_battery_high == 66
        assert row.body_battery_low == 66


class TestBackfillApply:
    def test_fixes_buggy_row(self, repo):
        from scripts.backfill_body_battery import backfill
        day = date(2026, 4, 5)
        repo.save_daily_metrics(day, {"body_battery_high": 66, "body_battery_low": 66})
        client = _make_garmin_client({day.isoformat(): _bb_response(10, 87)})

        backfill(repo, client, date(2026, 4, 1), date(2026, 4, 10), apply=True)

        row = repo.get_metrics_by_date(day)
        assert row.body_battery_high == 87
        assert row.body_battery_low == 10

    def test_skips_row_that_is_not_buggy(self, repo):
        """high != low means it was already synced correctly (post-fix) — leave it alone."""
        from scripts.backfill_body_battery import backfill
        day = date(2026, 4, 5)
        repo.save_daily_metrics(day, {"body_battery_high": 90, "body_battery_low": 20})
        client = _make_garmin_client({day.isoformat(): _bb_response(1, 99)})

        backfill(repo, client, date(2026, 4, 1), date(2026, 4, 10), apply=True)

        row = repo.get_metrics_by_date(day)
        assert row.body_battery_high == 90
        assert row.body_battery_low == 20

    def test_skips_row_with_no_body_battery_at_all(self, repo):
        from scripts.backfill_body_battery import backfill
        day = date(2026, 4, 5)
        repo.save_daily_metrics(day, {"steps": 5000})
        client = _make_garmin_client({})

        backfill(repo, client, date(2026, 4, 1), date(2026, 4, 10), apply=True)

        row = repo.get_metrics_by_date(day)
        assert row.body_battery_high is None

    def test_unrecoverable_day_left_untouched(self, repo):
        """A buggy row whose Garmin data has no real level readings (pre-cutover) stays as-is."""
        from scripts.backfill_body_battery import backfill
        day = date(2026, 4, 5)
        repo.save_daily_metrics(day, {"body_battery_high": 66, "body_battery_low": 66})
        client = _make_garmin_client({day.isoformat(): _bb_no_levels()})

        backfill(repo, client, date(2026, 4, 1), date(2026, 4, 10), apply=True)

        row = repo.get_metrics_by_date(day)
        assert row.body_battery_high == 66
        assert row.body_battery_low == 66

    def test_start_before_cutover_is_clamped(self, repo):
        from scripts.backfill_body_battery import backfill, EARLIEST_RECOVERABLE
        day = EARLIEST_RECOVERABLE
        repo.save_daily_metrics(day, {"body_battery_high": 66, "body_battery_low": 66})
        earlier_buggy_day = date(2026, 2, 1)
        repo.save_daily_metrics(earlier_buggy_day, {"body_battery_high": 40, "body_battery_low": 40})
        client = _make_garmin_client({day.isoformat(): _bb_response(5, 90)})

        backfill(repo, client, date(2026, 1, 1), date(2026, 4, 10), apply=True)

        assert repo.get_metrics_by_date(day).body_battery_high == 90
        # Pre-cutover day is never even queried against Garmin — left untouched.
        assert repo.get_metrics_by_date(earlier_buggy_day).body_battery_high == 40

    def test_one_failure_does_not_abort_the_rest(self, repo):
        from scripts.backfill_body_battery import backfill
        day1, day2 = date(2026, 4, 5), date(2026, 4, 6)
        repo.save_daily_metrics(day1, {"body_battery_high": 66, "body_battery_low": 66})
        repo.save_daily_metrics(day2, {"body_battery_high": 50, "body_battery_low": 50})
        client = _make_garmin_client({day2.isoformat(): _bb_response(3, 95)})
        client._ensure_authenticated.return_value.get_body_battery.side_effect = (
            lambda d: (_ for _ in ()).throw(RuntimeError("Garmin down"))
            if d == day1.isoformat() else _bb_response(3, 95)
        )

        backfill(repo, client, date(2026, 4, 1), date(2026, 4, 10), apply=True)

        assert repo.get_metrics_by_date(day1).body_battery_high == 66
        assert repo.get_metrics_by_date(day2).body_battery_high == 95
