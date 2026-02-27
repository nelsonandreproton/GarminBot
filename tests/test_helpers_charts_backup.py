"""Tests for:
  - src/telegram/helpers.py  (safe_command decorator, _is_rate_limited, _row_to_metrics)
  - src/utils/charts.py      (weekly, monthly, weight-trend chart generation)
  - src/utils/backup.py      (create_backup, _prune_old_backups)
"""

from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


# ------------------------------------------------------------------ #
# Helpers: safe_command                                               #
# ------------------------------------------------------------------ #

from src.telegram.helpers import safe_command


class _FakeBot:
    """Minimal host object for @safe_command-decorated methods."""

    @safe_command
    async def _cmd_ok(self, update, context):
        return 42

    @safe_command
    async def _cmd_raises(self, update, context):
        raise RuntimeError("boom")


def _make_update(reply_mock=None):
    msg = MagicMock()
    msg.reply_text = reply_mock or AsyncMock()
    update = MagicMock()
    update.message = msg
    return update


def _run(coro):
    return asyncio.run(coro)


def test_safe_command_passes_return_value():
    result = _run(_FakeBot()._cmd_ok(_make_update(), None))
    assert result == 42


def test_safe_command_catches_exception_without_raising():
    # Must not propagate the RuntimeError
    _run(_FakeBot()._cmd_raises(_make_update(), None))


def test_safe_command_sends_error_message_to_user():
    reply_mock = AsyncMock()
    _run(_FakeBot()._cmd_raises(_make_update(reply_mock), None))
    reply_mock.assert_awaited_once()
    sent_text = reply_mock.call_args[0][0]
    assert "erro" in sent_text.lower()


def test_safe_command_no_crash_when_message_is_none():
    """Inner try/except must not raise even if update.message is None."""
    update = MagicMock()
    update.message = None
    _run(_FakeBot()._cmd_raises(update, None))


def test_safe_command_preserves_function_name():
    assert _FakeBot._cmd_ok.__name__ == "_cmd_ok"
    assert _FakeBot._cmd_raises.__name__ == "_cmd_raises"


# ------------------------------------------------------------------ #
# Helpers: _row_to_metrics                                            #
# ------------------------------------------------------------------ #

from src.telegram.helpers import _row_to_metrics


def _make_orm_row(**overrides):
    defaults = dict(
        date=date(2026, 2, 25),
        sleep_hours=7.5,
        sleep_score=80,
        sleep_quality="Boa",
        sleep_deep_min=90,
        sleep_light_min=180,
        sleep_rem_min=100,
        sleep_awake_min=20,
        steps=9500,
        active_calories=450,
        resting_calories=1800,
        total_calories=2250,
        floors_ascended=5,
        intensity_moderate_min=30,
        intensity_vigorous_min=10,
        resting_heart_rate=55,
        avg_stress=35,
        body_battery_high=90,
        body_battery_low=20,
        spo2_avg=97.0,
        weight_kg=78.5,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_row_to_metrics_maps_all_fields():
    row = _make_orm_row()
    m = _row_to_metrics(row)
    assert m["date"] == date(2026, 2, 25)
    assert m["sleep_hours"] == 7.5
    assert m["steps"] == 9500
    assert m["active_calories"] == 450
    assert m["resting_heart_rate"] == 55
    assert m["weight_kg"] == 78.5
    assert m["spo2_avg"] == 97.0


def test_row_to_metrics_optional_fields_default_none():
    """getattr-based fields return None if the row doesn't have them."""
    row = SimpleNamespace(
        date=date(2026, 2, 25),
        sleep_hours=7.0, sleep_score=75, sleep_quality="Boa",
        steps=8000, active_calories=400, resting_calories=1800, total_calories=2200,
        resting_heart_rate=60, avg_stress=40,
        body_battery_high=85, body_battery_low=25,
        weight_kg=None,
    )
    m = _row_to_metrics(row)
    assert m["sleep_deep_min"] is None
    assert m["spo2_avg"] is None
    assert m["floors_ascended"] is None


# ------------------------------------------------------------------ #
# Charts                                                               #
# ------------------------------------------------------------------ #

from src.utils.charts import (
    generate_monthly_chart,
    generate_weekly_chart,
    generate_weight_trend_chart,
)


def _chart_row(d: date, steps: int = 8000, sleep_hours: float = 7.0, weight_kg=None):
    return SimpleNamespace(date=d, steps=steps, sleep_hours=sleep_hours, weight_kg=weight_kg)


def test_generate_weekly_chart_returns_png_bytes():
    rows = [_chart_row(date(2026, 2, d)) for d in range(18, 25)]
    result = generate_weekly_chart(rows)
    assert isinstance(result, bytes) and len(result) > 0


def test_generate_weekly_chart_with_goals():
    rows = [_chart_row(date(2026, 2, d), steps=10_000 + d * 100) for d in range(18, 25)]
    result = generate_weekly_chart(rows, goals={"steps": 9_000, "sleep_hours": 7.5})
    assert isinstance(result, bytes)


def test_generate_weekly_chart_with_weight_panel():
    rows = [_chart_row(date(2026, 2, d), weight_kg=78.0 - d * 0.1) for d in range(18, 25)]
    result = generate_weekly_chart(rows, goals={"weight_kg": 75.0})
    assert isinstance(result, bytes)


def test_generate_weekly_chart_with_deficit_panel():
    rows = [_chart_row(date(2026, 2, d)) for d in range(18, 25)]
    deficits = [200, -100, 300, None, 150, 250, 100]
    result = generate_weekly_chart(rows, deficits=deficits)
    assert isinstance(result, bytes)


def test_generate_weekly_chart_all_deficits_none():
    rows = [_chart_row(date(2026, 2, d)) for d in range(18, 25)]
    result = generate_weekly_chart(rows, deficits=[None] * 7)
    assert isinstance(result, bytes)


def test_generate_monthly_chart_returns_png_bytes():
    rows = [_chart_row(date(2026, 1, (d % 28) + 1), steps=7_000 + d * 50) for d in range(30)]
    result = generate_monthly_chart(rows)
    assert isinstance(result, bytes) and len(result) > 0


def test_generate_monthly_chart_with_goals():
    rows = [_chart_row(date(2026, 1, (d % 28) + 1)) for d in range(30)]
    result = generate_monthly_chart(rows, goals={"steps": 8_000, "sleep_hours": 7.0})
    assert isinstance(result, bytes)


def test_generate_monthly_chart_fewer_than_7_rows():
    """Fewer than 7 rows → moving average is all None; should still produce a chart."""
    rows = [_chart_row(date(2026, 2, d)) for d in range(18, 23)]  # 5 rows
    result = generate_monthly_chart(rows)
    assert isinstance(result, bytes)


def test_generate_weight_trend_chart_returns_png_bytes():
    records = [(date(2026, 1, d + 1), 80.0 - d * 0.05) for d in range(15)]
    result = generate_weight_trend_chart(records, weight_goal=75.0)
    assert isinstance(result, bytes) and len(result) > 0


def test_generate_weight_trend_chart_no_goal():
    records = [(date(2026, 1, d + 1), 78.0 - d * 0.1) for d in range(10)]
    result = generate_weight_trend_chart(records)
    assert isinstance(result, bytes)


def test_generate_weight_trend_chart_needs_at_least_2_records():
    assert generate_weight_trend_chart([]) is None
    assert generate_weight_trend_chart([(date(2026, 1, 1), 80.0)]) is None


# ------------------------------------------------------------------ #
# Backup                                                               #
# ------------------------------------------------------------------ #


@pytest.fixture
def backup_dir(tmp_path, monkeypatch):
    """Redirect backup module's _BACKUP_DIR to an isolated temp directory."""
    import src.utils.backup as backup_mod
    bd = tmp_path / "backups"
    monkeypatch.setattr(backup_mod, "_BACKUP_DIR", bd)
    return bd


@pytest.fixture
def source_db(tmp_path):
    """Minimal file that looks like a database to back up."""
    db = tmp_path / "test.db"
    db.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
    return db


def test_create_backup_creates_file(backup_dir, source_db):
    from src.utils.backup import create_backup
    result = create_backup(str(source_db))
    assert result is not None
    assert result.exists()
    assert result.suffix == ".db"


def test_create_backup_content_matches(backup_dir, source_db):
    from src.utils.backup import create_backup
    result = create_backup(str(source_db))
    assert result.read_bytes() == source_db.read_bytes()


def test_create_backup_returns_none_for_missing_source(backup_dir, tmp_path):
    from src.utils.backup import create_backup
    result = create_backup(str(tmp_path / "nonexistent.db"))
    assert result is None


def test_create_backup_creates_backup_dir_if_missing(backup_dir, source_db):
    from src.utils.backup import create_backup
    assert not backup_dir.exists()
    create_backup(str(source_db))
    assert backup_dir.exists()


def test_prune_keeps_last_7(backup_dir):
    import src.utils.backup as backup_mod
    backup_dir.mkdir(parents=True, exist_ok=True)
    for i in range(9):
        (backup_dir / f"garmin_data_202601{i:02d}_120000.db").write_bytes(b"x")
    backup_mod._prune_old_backups()
    remaining = list(backup_dir.glob("garmin_data_*.db"))
    assert len(remaining) == 7


def test_prune_removes_oldest(backup_dir):
    import src.utils.backup as backup_mod
    backup_dir.mkdir(parents=True, exist_ok=True)
    names = [f"garmin_data_202601{i:02d}_120000.db" for i in range(9)]
    for n in names:
        (backup_dir / n).write_bytes(b"x")
    backup_mod._prune_old_backups()
    remaining = {p.name for p in backup_dir.glob("garmin_data_*.db")}
    # Two oldest gone
    assert names[0] not in remaining
    assert names[1] not in remaining
    # Seven newest kept
    for n in names[-7:]:
        assert n in remaining


def test_prune_no_op_when_fewer_than_7(backup_dir):
    import src.utils.backup as backup_mod
    backup_dir.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (backup_dir / f"garmin_data_202601{i:02d}_120000.db").write_bytes(b"x")
    backup_mod._prune_old_backups()
    assert len(list(backup_dir.glob("garmin_data_*.db"))) == 5


def test_create_backup_triggers_prune_on_overflow(backup_dir, source_db):
    """create_backup must prune after writing; excess old backups get removed."""
    import src.utils.backup as backup_mod
    backup_dir.mkdir(parents=True, exist_ok=True)
    # 8 pre-existing backups
    for i in range(8):
        (backup_dir / f"garmin_data_20260101_{i:06d}.db").write_bytes(b"old")
    backup_mod.create_backup(str(source_db))
    # 8 old + 1 new = 9 → prune to 7
    assert len(list(backup_dir.glob("garmin_data_*.db"))) == 7
