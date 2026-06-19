"""Tests for Repository read_only=True mode.

TDD: fix applied after confirming this test fails without the parameter.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import OperationalError

from src.database.repository import Repository


def test_readonly_repo_allows_reads_and_blocks_writes(tmp_path):
    """A read-only repo should be able to read data but refuse all writes."""
    db_file = str(tmp_path / "test.db")

    # --- Step 1: create RW repo, init schema, save one row ---
    rw_repo = Repository(db_file)
    rw_repo.init_database()
    rw_repo.save_daily_metrics(date(2024, 6, 1), {
        "sleep_hours": 7.5,
        "steps": 9000,
        "active_calories": 400,
        "resting_calories": 1800,
        "total_calories": 2200,
        "garmin_sync_success": True,
    })
    rw_repo._engine.dispose()  # release Windows file lock before opening RO

    # --- Step 2: open RO repo, read the saved row ---
    ro_repo = Repository(db_file, read_only=True)
    try:
        row = ro_repo.get_metrics_by_date(date(2024, 6, 1))
        assert row is not None, "read-only repo must be able to read existing rows"
        assert row.steps == 9000

        # --- Step 3: assert write raises ---
        with pytest.raises(Exception) as exc_info:
            ro_repo.save_daily_metrics(date(2024, 6, 2), {
                "sleep_hours": 8.0,
                "steps": 10000,
                "garmin_sync_success": True,
            })
        err_msg = str(exc_info.value).lower()
        assert "readonly" in err_msg or "read-only" in err_msg or "read only" in err_msg, (
            f"Expected a read-only error, got: {exc_info.value}"
        )
    finally:
        ro_repo._engine.dispose()  # always release before tmp_path cleanup
