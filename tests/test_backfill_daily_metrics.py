"""Tests for scripts/backfill_daily_metrics.py — fills days with no
daily_metrics row at all, beyond the Telegram /backfill command's 30-day cap.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.database.repository import Repository


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch("scripts.backfill_daily_metrics.time.sleep"):
        yield


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


def _make_garmin_client(summary_by_date: dict):
    client = MagicMock()

    def _summary(day):
        if day not in summary_by_date:
            raise RuntimeError(f"no summary configured for {day}")
        return summary_by_date[day]

    client.get_summary_for_date.side_effect = _summary
    client.to_metrics_dict.side_effect = lambda summary: summary
    return client


def _metrics(steps: int = 8000, garmin_sync_success: bool = True) -> dict:
    return {"steps": steps, "garmin_sync_success": garmin_sync_success}


class TestBackfillDryRun:
    def test_dry_run_makes_no_repo_writes(self, repo):
        from scripts.backfill_daily_metrics import backfill
        day = date(2026, 1, 5)
        client = _make_garmin_client({day: _metrics()})

        backfill(repo, client, date(2026, 1, 1), date(2026, 1, 10), apply=False)

        assert repo.get_metrics_by_date(day) is None


class TestBackfillApply:
    def test_fills_missing_day(self, repo):
        from scripts.backfill_daily_metrics import backfill
        day = date(2026, 1, 5)
        client = _make_garmin_client({day: _metrics(steps=9000)})

        backfill(repo, client, date(2026, 1, 1), date(2026, 1, 10), apply=True)

        row = repo.get_metrics_by_date(day)
        assert row is not None
        assert row.steps == 9000

    def test_skips_day_already_present(self, repo):
        """get_missing_dates excludes days that already have a row — the
        already-present day must never be re-fetched or overwritten."""
        from scripts.backfill_daily_metrics import backfill
        day = date(2026, 1, 5)
        repo.save_daily_metrics(day, {"steps": 1234})
        client = _make_garmin_client({})

        backfill(repo, client, day, day, apply=True)

        assert client.get_summary_for_date.call_count == 0
        row = repo.get_metrics_by_date(day)
        assert row.steps == 1234

    def test_does_not_fetch_activities(self, repo):
        """Activities are already covered by backfill_activities.py's range
        fetch — re-fetching here would duplicate work and roughly double the
        API call count for zero new data."""
        from scripts.backfill_daily_metrics import backfill
        day = date(2026, 1, 5)
        client = _make_garmin_client({day: _metrics()})

        backfill(repo, client, date(2026, 1, 1), date(2026, 1, 10), apply=True)

        assert client.get_activities_for_date.call_count == 0

    def test_stops_on_rate_limit_without_failing_the_whole_run(self, repo):
        from scripts.backfill_daily_metrics import backfill
        day1, day2, day3 = date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)
        client = _make_garmin_client({day1: _metrics(), day3: _metrics()})
        client.get_summary_for_date.side_effect = [
            _metrics(),
            RuntimeError("429 Too Many Requests"),
            _metrics(),
        ]

        backfill(repo, client, date(2026, 1, 5), date(2026, 1, 7), apply=True)

        assert repo.get_metrics_by_date(day1) is not None
        assert repo.get_metrics_by_date(day2) is None
        assert repo.get_metrics_by_date(day3) is None  # never reached — loop stopped

    def test_one_failure_does_not_abort_the_rest(self, repo):
        from scripts.backfill_daily_metrics import backfill
        day1, day2 = date(2026, 1, 5), date(2026, 1, 6)
        client = _make_garmin_client({day2: _metrics(steps=5555)})
        client.get_summary_for_date.side_effect = [
            RuntimeError("transient failure"),
            _metrics(steps=5555),
        ]

        backfill(repo, client, date(2026, 1, 5), date(2026, 1, 6), apply=True)

        assert repo.get_metrics_by_date(day1) is None
        assert repo.get_metrics_by_date(day2).steps == 5555

    def test_no_missing_days_does_not_crash(self, repo):
        from scripts.backfill_daily_metrics import backfill
        repo.save_daily_metrics(date(2026, 1, 1), {"steps": 1})
        repo.save_daily_metrics(date(2026, 1, 2), {"steps": 1})
        client = _make_garmin_client({})
        backfill(repo, client, date(2026, 1, 1), date(2026, 1, 2), apply=True)
        assert client.get_summary_for_date.call_count == 0

    def test_sleeps_after_a_failure_too_not_just_after_success(self, repo):
        """Prove-It: the pacing sleep must run on every iteration — skipping it
        on the failure path would make a stretch of failures hit Garmin faster,
        the opposite of what pacing is for."""
        from scripts.backfill_daily_metrics import backfill
        day1, day2 = date(2026, 1, 5), date(2026, 1, 6)
        client = _make_garmin_client({day2: _metrics()})
        client.get_summary_for_date.side_effect = [RuntimeError("transient"), _metrics()]

        with patch("scripts.backfill_daily_metrics.time.sleep") as mock_sleep:
            backfill(repo, client, day1, day2, apply=True)

        assert mock_sleep.call_count == 2
