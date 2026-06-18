"""Tests for FatSecret integration in make_sync_job (graceful degradation)."""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.database.repository import Repository
from src.scheduler.jobs import make_sync_job


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


def _make_garmin_mock(day: date | None = None):
    """Build a mock GarminClient that returns a minimal summary for the given day."""
    if day is None:
        day = date.today() - timedelta(days=1)
    garmin = MagicMock()
    summary = MagicMock()
    summary.date = day
    garmin.get_yesterday_summary.return_value = summary
    garmin.to_metrics_dict.return_value = {"garmin_sync_success": True, "steps": 8000}
    return garmin


def _make_fatsecret_mock(raw_entries=None, raises=None):
    """Build a mock FatSecretClient."""
    fs = MagicMock()
    if raises is not None:
        fs.get_food_entries.side_effect = raises
    else:
        fs.get_food_entries.return_value = raw_entries or []
    return fs


def _mapped_entries():
    """Sample mapped entries (as map_fatsecret_entries would return)."""
    return [
        {
            "name": "Oats",
            "calories": 300.0,
            "protein_g": 10.0,
            "fat_g": 5.0,
            "carbs_g": 50.0,
            "fiber_g": 4.0,
            "quantity": 1.0,
            "unit": "serving",
            "source": "fatsecret",
            "barcode": "FS001",
        },
        {
            "name": "Whey",
            "calories": 120.0,
            "protein_g": 25.0,
            "fat_g": 1.0,
            "carbs_g": 3.0,
            "fiber_g": 0.0,
            "quantity": 1.0,
            "unit": "serving",
            "source": "fatsecret",
            "barcode": "FS002",
        },
    ]


# ---------------------------------------------------------------------------
# FatSecret=None: backward-compat
# ---------------------------------------------------------------------------

class TestSyncJobNoFatSecret:
    def test_sync_works_without_fatsecret(self, repo):
        """Existing behavior: fatsecret=None works fine (backward-compat)."""
        garmin = _make_garmin_mock()
        job = make_sync_job(garmin, repo)
        job()  # must not raise
        yesterday = date.today() - timedelta(days=1)
        assert repo.get_metrics_by_date(yesterday) is not None

    def test_sync_status_logged_as_success_without_fatsecret(self, repo):
        garmin = _make_garmin_mock()
        job = make_sync_job(garmin, repo)
        job()
        last = repo.get_last_successful_sync()
        assert last is not None
        assert last.status == "success"


# ---------------------------------------------------------------------------
# FatSecret provided: happy path
# ---------------------------------------------------------------------------

class TestSyncJobWithFatSecret:
    def test_fatsecret_entries_persisted_after_sync(self, repo):
        """Sync job must call repo.upsert_fatsecret_entries when fatsecret is given."""
        day = date.today() - timedelta(days=1)
        garmin = _make_garmin_mock(day)
        fs = _make_fatsecret_mock()

        mapped = _mapped_entries()

        with patch("src.scheduler.jobs.map_fatsecret_entries", return_value=mapped):
            job = make_sync_job(garmin, repo, fatsecret=fs)
            job()

        entries = repo.get_food_entries(day)
        assert len(entries) == 2

    def test_fatsecret_called_with_garmin_date(self, repo):
        """FatSecret must be called with the same date as the Garmin summary."""
        day = date(2026, 6, 10)
        garmin = _make_garmin_mock(day)
        fs = _make_fatsecret_mock()

        with patch("src.scheduler.jobs.map_fatsecret_entries", return_value=[]):
            job = make_sync_job(garmin, repo, fatsecret=fs)
            job()

        fs.get_food_entries.assert_called_once_with(day)

    def test_garmin_data_also_saved_with_fatsecret(self, repo):
        """Garmin metrics must still be saved when fatsecret is present."""
        day = date.today() - timedelta(days=1)
        garmin = _make_garmin_mock(day)
        fs = _make_fatsecret_mock()

        with patch("src.scheduler.jobs.map_fatsecret_entries", return_value=[]):
            job = make_sync_job(garmin, repo, fatsecret=fs)
            job()

        assert repo.get_metrics_by_date(day) is not None

    def test_sync_status_is_success_with_fatsecret(self, repo):
        """Sync log status must be success even when FatSecret also runs."""
        day = date.today() - timedelta(days=1)
        garmin = _make_garmin_mock(day)
        fs = _make_fatsecret_mock()

        with patch("src.scheduler.jobs.map_fatsecret_entries", return_value=_mapped_entries()):
            job = make_sync_job(garmin, repo, fatsecret=fs)
            job()

        last = repo.get_last_successful_sync()
        assert last is not None
        assert last.status == "success"

    def test_nutrition_total_correct_after_sync(self, repo):
        """Daily nutrition total must reflect the FatSecret entries."""
        day = date.today() - timedelta(days=1)
        garmin = _make_garmin_mock(day)
        fs = _make_fatsecret_mock()

        with patch("src.scheduler.jobs.map_fatsecret_entries", return_value=_mapped_entries()):
            job = make_sync_job(garmin, repo, fatsecret=fs)
            job()

        nutrition = repo.get_daily_nutrition(day)
        # 300 + 120 = 420 total calories
        assert nutrition["calories"] == pytest.approx(420.0)

    def test_idempotent_sync_does_not_double_calories(self, repo):
        """Running the sync twice must not double the nutrition totals."""
        day = date.today() - timedelta(days=1)
        garmin = _make_garmin_mock(day)
        fs = _make_fatsecret_mock()

        with patch("src.scheduler.jobs.map_fatsecret_entries", return_value=_mapped_entries()):
            job = make_sync_job(garmin, repo, fatsecret=fs)
            job()
            job()

        nutrition = repo.get_daily_nutrition(day)
        assert nutrition["calories"] == pytest.approx(420.0)


# ---------------------------------------------------------------------------
# Graceful degradation: FatSecret failure must NOT break the Garmin sync
# ---------------------------------------------------------------------------

class TestSyncJobFatSecretDegradation:
    def test_garmin_data_saved_when_fatsecret_raises(self, repo):
        """Garmin metrics must be persisted even if fatsecret.get_food_entries raises."""
        day = date.today() - timedelta(days=1)
        garmin = _make_garmin_mock(day)
        fs = _make_fatsecret_mock(raises=RuntimeError("API exploded"))

        job = make_sync_job(garmin, repo, fatsecret=fs)
        job()  # must NOT raise

        assert repo.get_metrics_by_date(day) is not None

    def test_job_does_not_reraise_when_fatsecret_raises(self, repo):
        """The sync job must not propagate FatSecret exceptions."""
        garmin = _make_garmin_mock()
        fs = _make_fatsecret_mock(raises=ConnectionError("timeout"))

        job = make_sync_job(garmin, repo, fatsecret=fs)
        # Must complete without raising
        job()

    def test_sync_log_remains_success_when_fatsecret_raises(self, repo):
        """Sync log status must stay success/partial — NOT error — on FatSecret failure."""
        day = date.today() - timedelta(days=1)
        garmin = _make_garmin_mock(day)
        fs = _make_fatsecret_mock(raises=Exception("FatSecret down"))

        job = make_sync_job(garmin, repo, fatsecret=fs)
        job()

        last = repo.get_last_successful_sync()
        assert last is not None
        # Must not have logged 'error' status for a successful Garmin sync
        assert last.status in ("success", "partial")

    def test_warning_logged_when_fatsecret_raises(self, repo, caplog):
        """A warning must be emitted when FatSecret sync fails."""
        garmin = _make_garmin_mock()
        fs = _make_fatsecret_mock(raises=RuntimeError("quota exceeded"))

        job = make_sync_job(garmin, repo, fatsecret=fs)
        with caplog.at_level(logging.WARNING, logger="src.scheduler.jobs"):
            job()

        assert any("fatsecret" in r.message.lower() for r in caplog.records)

    def test_fatsecret_map_error_does_not_break_sync(self, repo):
        """If map_fatsecret_entries itself raises, Garmin sync still succeeds."""
        day = date.today() - timedelta(days=1)
        garmin = _make_garmin_mock(day)
        fs = _make_fatsecret_mock(raw_entries=[{"bad": "data"}])

        with patch("src.scheduler.jobs.map_fatsecret_entries", side_effect=ValueError("bad mapping")):
            job = make_sync_job(garmin, repo, fatsecret=fs)
            job()  # must not raise

        assert repo.get_metrics_by_date(day) is not None
