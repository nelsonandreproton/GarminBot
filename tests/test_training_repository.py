"""Tests for UserSetting and TrainingEntry repository methods."""

from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

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


# ------------------------------------------------------------------ #
# UserSetting                                                          #
# ------------------------------------------------------------------ #

def test_get_setting_missing_returns_none(repo):
    assert repo.get_setting("nonexistent_key") is None


def test_set_and_get_setting(repo):
    repo.set_setting("gym_equipment", "halteres 2-20kg")
    assert repo.get_setting("gym_equipment") == "halteres 2-20kg"


def test_set_setting_update(repo):
    repo.set_setting("gym_equipment", "halteres")
    repo.set_setting("gym_equipment", "barbell + rack")
    assert repo.get_setting("gym_equipment") == "barbell + rack"


def test_set_setting_multiple_keys(repo):
    repo.set_setting("gym_equipment", "halteres")
    repo.set_setting("gym_training_minutes", "60")
    assert repo.get_setting("gym_equipment") == "halteres"
    assert repo.get_setting("gym_training_minutes") == "60"


# ------------------------------------------------------------------ #
# TrainingEntry                                                        #
# ------------------------------------------------------------------ #

def test_upsert_training_entry_insert(repo):
    d = date(2026, 2, 25)
    repo.upsert_training_entry(d, "Bench press 4x8, Pull-ups 3x10")
    entries = repo.get_recent_training(30)
    assert len(entries) == 1
    assert entries[0].date == d
    assert entries[0].description == "Bench press 4x8, Pull-ups 3x10"


def test_upsert_training_entry_update_same_day(repo):
    d = date(2026, 2, 25)
    repo.upsert_training_entry(d, "First entry")
    repo.upsert_training_entry(d, "Updated entry")
    entries = repo.get_recent_training(30)
    assert len(entries) == 1
    assert entries[0].description == "Updated entry"


def test_get_recent_training_empty(repo):
    assert repo.get_recent_training(7) == []


def test_get_recent_training_respects_days_window(repo):
    today = date.today()
    # Inside window (last 7 days)
    repo.upsert_training_entry(today - timedelta(days=1), "Treino recente")
    repo.upsert_training_entry(today - timedelta(days=6), "Treino limite")
    # Outside window (8 days ago, cutoff is strictly > today-7)
    repo.upsert_training_entry(today - timedelta(days=8), "Treino antigo")

    entries = repo.get_recent_training(7)
    dates = {e.date for e in entries}
    assert (today - timedelta(days=1)) in dates
    assert (today - timedelta(days=6)) in dates
    assert (today - timedelta(days=8)) not in dates


def test_get_recent_training_ordered_desc(repo):
    for i in range(1, 4):
        repo.upsert_training_entry(date(2026, 2, 20) + timedelta(days=i), f"Treino {i}")
    entries = repo.get_recent_training(30)
    dates = [e.date for e in entries]
    assert dates == sorted(dates, reverse=True)


def test_get_recent_training_multiple_days(repo):
    today = date.today()
    for i in range(1, 6):
        repo.upsert_training_entry(today - timedelta(days=i), f"Treino {i}")
    entries = repo.get_recent_training(7)
    assert len(entries) == 5
