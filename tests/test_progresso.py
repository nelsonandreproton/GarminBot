"""Tests for B6 training progression: search_training_entries and formatter."""

import os
import tempfile
from datetime import date, timedelta

import pytest

from src.database.repository import Repository
from src.telegram.formatters import format_training_progression


# ------------------------------------------------------------------ #
# Fixtures                                                              #
# ------------------------------------------------------------------ #

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
# Repository: search_training_entries                                 #
# ------------------------------------------------------------------ #

def test_search_empty_db(repo):
    result = repo.search_training_entries("bench press")
    assert result == []


def test_search_finds_matching_entry(repo):
    repo.upsert_training_entry(date(2026, 2, 15), "Bench press 4x8 @80kg, Squat 4x10")
    result = repo.search_training_entries("bench press")
    assert len(result) == 1
    assert result[0]["date"] == date(2026, 2, 15)
    assert "Bench press" in result[0]["description"]


def test_search_case_insensitive(repo):
    repo.upsert_training_entry(date(2026, 2, 15), "BENCH PRESS 4x8 @80kg")
    result = repo.search_training_entries("bench press")
    assert len(result) == 1


def test_search_no_match(repo):
    repo.upsert_training_entry(date(2026, 2, 15), "Squat 4x10, Leg press 4x12")
    result = repo.search_training_entries("bench press")
    assert result == []


def test_search_returns_oldest_first(repo):
    repo.upsert_training_entry(date(2026, 2, 25), "Bench press 4x8 @82kg")
    repo.upsert_training_entry(date(2026, 2, 15), "Bench press 3x10 @75kg")
    repo.upsert_training_entry(date(2026, 2, 20), "Bench press 4x10 @78kg")
    result = repo.search_training_entries("bench press")
    assert len(result) == 3
    assert result[0]["date"] == date(2026, 2, 15)
    assert result[1]["date"] == date(2026, 2, 20)
    assert result[2]["date"] == date(2026, 2, 25)


def test_search_partial_match(repo):
    repo.upsert_training_entry(date(2026, 2, 15), "Incline bench press 3x8 @60kg")
    result = repo.search_training_entries("bench")
    assert len(result) == 1


def test_search_respects_limit(repo):
    for i in range(35):
        repo.upsert_training_entry(
            date(2026, 1, 1) + timedelta(days=i),
            f"Bench press {i+1}x8 @{60+i}kg",
        )
    result = repo.search_training_entries("bench", limit=10)
    assert len(result) == 10


def test_search_multiple_exercises_same_entry(repo):
    repo.upsert_training_entry(
        date(2026, 2, 15),
        "Bench press 4x8, Squat 4x10, Pull-ups 3x10",
    )
    bench_results = repo.search_training_entries("bench")
    squat_results = repo.search_training_entries("squat")
    assert len(bench_results) == 1
    assert len(squat_results) == 1


# ------------------------------------------------------------------ #
# Formatter: format_training_progression                             #
# ------------------------------------------------------------------ #

def test_format_progression_empty():
    text = format_training_progression("bench press", [])
    assert "bench press" in text.lower()
    assert "Nenhum registo" in text or "encontrado" in text


def test_format_progression_single_entry():
    entries = [{"date": date(2026, 2, 15), "description": "Bench press 4x8 @80kg"}]
    text = format_training_progression("bench press", entries)
    assert "bench press" in text.lower()
    assert "15/02/2026" in text
    assert "4x8" in text
    assert "1 registo" in text


def test_format_progression_multiple_entries():
    entries = [
        {"date": date(2026, 2, 1), "description": "Bench press 3x10 @75kg"},
        {"date": date(2026, 2, 8), "description": "Bench press 4x8 @78kg"},
        {"date": date(2026, 2, 15), "description": "Bench press 4x8 @80kg"},
    ]
    text = format_training_progression("bench press", entries)
    assert "3 registos" in text
    assert "01/02/2026" in text
    assert "15/02/2026" in text
