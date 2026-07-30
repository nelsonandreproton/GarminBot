"""Tests for OutSystems integration in make_sync_job (graceful degradation)."""

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


def _make_garmin_mock(day: date | None = None, metrics: dict | None = None):
    if day is None:
        day = date.today() - timedelta(days=1)
    garmin = MagicMock()
    summary = MagicMock()
    summary.date = day
    garmin.get_yesterday_summary.return_value = summary
    garmin.to_metrics_dict.return_value = metrics or {
        "garmin_sync_success": True,
        "steps": 8000,
        "active_calories": 500,
        "resting_calories": 1600,
        "weight_kg": 85.5,
    }
    return garmin


def _make_outsystems_mock(exists=False, create_raises=None, exists_raises=None):
    client = MagicMock()
    if exists_raises is not None:
        client.record_exists.side_effect = exists_raises
    else:
        client.record_exists.return_value = exists
    if create_raises is not None:
        client.create_record.side_effect = create_raises
    return client


# ---------------------------------------------------------------------------
# outsystems=None: backward-compat
# ---------------------------------------------------------------------------

class TestSyncJobNoOutSystems:
    def test_sync_works_without_outsystems(self, repo):
        garmin = _make_garmin_mock()
        job = make_sync_job(garmin, repo)
        result = job()  # must not raise
        yesterday = date.today() - timedelta(days=1)
        assert repo.get_metrics_by_date(yesterday) is not None
        assert result["warnings"] == []


# ---------------------------------------------------------------------------
# Happy path: record doesn't exist yet -> created
# ---------------------------------------------------------------------------

class TestSyncJobCreatesRecord:
    def test_creates_record_when_not_existing(self, repo):
        day = date.today() - timedelta(days=1)
        garmin = _make_garmin_mock(day)
        outsystems = _make_outsystems_mock(exists=False)

        job = make_sync_job(garmin, repo, outsystems=outsystems)
        result = job()

        outsystems.record_exists.assert_called_once_with(day)
        outsystems.create_record.assert_called_once()
        assert result["warnings"] == []

    def test_create_record_payload_uses_synced_day_and_nutrition(self, repo):
        day = date.today() - timedelta(days=1)
        garmin = _make_garmin_mock(day, metrics={
            "garmin_sync_success": True,
            "steps": 8000,
            "active_calories": 500,
            "resting_calories": 1600,
            "weight_kg": 85.5,
        })
        outsystems = _make_outsystems_mock(exists=False)

        job = make_sync_job(garmin, repo, outsystems=outsystems)
        job()

        _, kwargs = outsystems.create_record.call_args
        assert kwargs["day"] == day
        assert kwargs["active"] == 500
        assert kwargs["rest"] == 1600
        assert kwargs["weight"] == 85.5
        assert kwargs["steps"] == 8000
        # No food logged in this DB -> nutrition totals are real zeros
        assert kwargs["food"] == 0.0
        assert kwargs["protein"] == 0.0
        assert kwargs["carbs"] == 0.0
        assert kwargs["fat"] == 0.0

    def test_skips_create_when_record_already_exists(self, repo):
        garmin = _make_garmin_mock()
        outsystems = _make_outsystems_mock(exists=True)

        job = make_sync_job(garmin, repo, outsystems=outsystems)
        result = job()

        outsystems.create_record.assert_not_called()
        assert result["warnings"] == []


# ---------------------------------------------------------------------------
# Missing core Garmin fields -> must NOT create a record with false zeros
# ---------------------------------------------------------------------------

class TestSyncJobSkipsOnMissingData:
    def test_skips_create_when_weight_missing(self, repo, caplog):
        """Missing data is an expected daily occurrence (e.g. no weigh-in) — log it,
        but don't surface it as a Telegram warning (that's reserved for real failures)."""
        garmin = _make_garmin_mock(metrics={
            "garmin_sync_success": True,
            "steps": 8000,
            "active_calories": 500,
            "resting_calories": 1600,
            "weight_kg": None,
        })
        outsystems = _make_outsystems_mock(exists=False)

        job = make_sync_job(garmin, repo, outsystems=outsystems)
        with caplog.at_level(logging.WARNING, logger="src.scheduler.jobs"):
            result = job()

        outsystems.create_record.assert_not_called()
        assert result["warnings"] == []
        assert any("outsystems" in r.message.lower() for r in caplog.records)

    def test_skips_create_when_steps_missing(self, repo):
        garmin = _make_garmin_mock(metrics={
            "garmin_sync_success": True,
            "steps": None,
            "active_calories": 500,
            "resting_calories": 1600,
            "weight_kg": 85.5,
        })
        outsystems = _make_outsystems_mock(exists=False)

        job = make_sync_job(garmin, repo, outsystems=outsystems)
        job()

        outsystems.create_record.assert_not_called()

    def test_skips_create_when_active_calories_missing(self, repo):
        garmin = _make_garmin_mock(metrics={
            "garmin_sync_success": True,
            "steps": 8000,
            "active_calories": None,
            "resting_calories": 1600,
            "weight_kg": 85.5,
        })
        outsystems = _make_outsystems_mock(exists=False)

        job = make_sync_job(garmin, repo, outsystems=outsystems)
        job()

        outsystems.create_record.assert_not_called()

    def test_skips_create_when_resting_calories_missing(self, repo):
        garmin = _make_garmin_mock(metrics={
            "garmin_sync_success": True,
            "steps": 8000,
            "active_calories": 500,
            "resting_calories": None,
            "weight_kg": 85.5,
        })
        outsystems = _make_outsystems_mock(exists=False)

        job = make_sync_job(garmin, repo, outsystems=outsystems)
        job()

        outsystems.create_record.assert_not_called()


# ---------------------------------------------------------------------------
# Graceful degradation: failures must not break the rest of /sync, but must
# surface a warning the caller (Telegram /sync) can show to the user.
# ---------------------------------------------------------------------------

class TestSyncJobGatesOnFatSecretFailure:
    def test_skips_create_when_fatsecret_raises(self, repo):
        """If FatSecret sync fails, nutrition totals (Food/Protein/Carbs/Fat) would
        be false zeros — must not create the OutSystems record for that day."""
        day = date.today() - timedelta(days=1)
        garmin = _make_garmin_mock(day)
        outsystems = _make_outsystems_mock(exists=False)
        fatsecret = MagicMock()
        fatsecret.get_food_entries.side_effect = RuntimeError("FatSecret down")

        job = make_sync_job(garmin, repo, fatsecret=fatsecret, outsystems=outsystems)
        job()

        outsystems.create_record.assert_not_called()

    def test_creates_when_fatsecret_absent(self, repo):
        """No FatSecret configured at all is NOT a failure — nutrition is genuinely
        zero (no food tracking), so OutSystems creation should proceed."""
        day = date.today() - timedelta(days=1)
        garmin = _make_garmin_mock(day)
        outsystems = _make_outsystems_mock(exists=False)

        job = make_sync_job(garmin, repo, fatsecret=None, outsystems=outsystems)
        job()

        outsystems.create_record.assert_called_once()

    def test_creates_when_fatsecret_succeeds(self, repo):
        day = date.today() - timedelta(days=1)
        garmin = _make_garmin_mock(day)
        outsystems = _make_outsystems_mock(exists=False)
        fatsecret = MagicMock()
        fatsecret.get_food_entries.return_value = []

        with patch("src.scheduler.jobs.map_fatsecret_entries", return_value=[]):
            job = make_sync_job(garmin, repo, fatsecret=fatsecret, outsystems=outsystems)
            job()

        outsystems.create_record.assert_called_once()


class TestSyncJobOutSystemsDegradation:
    def test_garmin_data_saved_when_record_exists_check_raises(self, repo):
        day = date.today() - timedelta(days=1)
        garmin = _make_garmin_mock(day)
        outsystems = _make_outsystems_mock(exists_raises=ConnectionError("unreachable"))

        job = make_sync_job(garmin, repo, outsystems=outsystems)
        result = job()  # must not raise

        assert repo.get_metrics_by_date(day) is not None
        assert any("outsystems" in w.lower() for w in result["warnings"])

    def test_does_not_create_when_exists_check_raises(self, repo):
        """Fail closed: if existence can't be verified, never POST (no update endpoint)."""
        garmin = _make_garmin_mock()
        outsystems = _make_outsystems_mock(exists_raises=ConnectionError("unreachable"))

        job = make_sync_job(garmin, repo, outsystems=outsystems)
        job()

        outsystems.create_record.assert_not_called()

    def test_garmin_data_saved_when_create_raises(self, repo):
        day = date.today() - timedelta(days=1)
        garmin = _make_garmin_mock(day)
        outsystems = _make_outsystems_mock(exists=False, create_raises=RuntimeError("500 error"))

        job = make_sync_job(garmin, repo, outsystems=outsystems)
        result = job()  # must not raise

        assert repo.get_metrics_by_date(day) is not None
        assert any("outsystems" in w.lower() for w in result["warnings"])

    def test_sync_log_remains_success_when_outsystems_raises(self, repo):
        day = date.today() - timedelta(days=1)
        garmin = _make_garmin_mock(day)
        outsystems = _make_outsystems_mock(exists=False, create_raises=Exception("boom"))

        job = make_sync_job(garmin, repo, outsystems=outsystems)
        job()

        last = repo.get_last_successful_sync()
        assert last is not None
        assert last.status in ("success", "partial")

    def test_warning_logged_when_outsystems_raises(self, repo, caplog):
        garmin = _make_garmin_mock()
        outsystems = _make_outsystems_mock(exists=False, create_raises=RuntimeError("boom"))

        job = make_sync_job(garmin, repo, outsystems=outsystems)
        with caplog.at_level(logging.WARNING, logger="src.scheduler.jobs"):
            job()

        assert any("outsystems" in r.message.lower() for r in caplog.records)
