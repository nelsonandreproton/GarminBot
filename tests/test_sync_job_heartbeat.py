"""Tests for the HetznerCheck heartbeat emitted by make_sync_job.

Sync only runs on a manual /sync (there is no scheduled job), so the heartbeat
must not set a deadline — otherwise HetznerCheck alerts every time the user
skips /sync for a day.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.database.repository import Repository
from src.scheduler import jobs
from src.scheduler.jobs import make_sync_job


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


def _make_garmin_mock():
    garmin = MagicMock()
    summary = MagicMock()
    summary.date = date.today() - timedelta(days=1)
    garmin.get_yesterday_summary.return_value = summary
    garmin.to_metrics_dict.return_value = {"garmin_sync_success": True, "steps": 8000}
    garmin.get_activities_for_date.return_value = []
    return garmin


def test_sync_heartbeat_on_success_sets_no_deadline(repo):
    hb = MagicMock()
    with patch.object(jobs, "_HEARTBEAT_AVAILABLE", True), patch.object(jobs, "_hb_beat", hb, create=True):
        make_sync_job(_make_garmin_mock(), repo)()

    hb.assert_called_once()
    assert hb.call_args.kwargs["status"] == "ok"
    assert hb.call_args.kwargs.get("next_in_seconds") is None


def test_sync_heartbeat_on_failure_sets_no_deadline(repo):
    garmin = _make_garmin_mock()
    garmin.get_yesterday_summary.side_effect = RuntimeError("boom")
    hb = MagicMock()
    with patch.object(jobs, "_HEARTBEAT_AVAILABLE", True), patch.object(jobs, "_hb_beat", hb, create=True):
        with pytest.raises(RuntimeError):
            make_sync_job(garmin, repo)()

    hb.assert_called_once()
    assert hb.call_args.kwargs["status"] == "error"
    assert hb.call_args.kwargs.get("next_in_seconds") is None
