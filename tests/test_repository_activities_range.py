"""Tests for Repository.get_garmin_activities_range — TDD, written before implementation."""

from __future__ import annotations

from datetime import date

import pytest

from src.database.repository import Repository


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "test.db")
    r = Repository(db_path)
    r.init_database()
    yield r
    r._engine.dispose()


def _seed_activity(repo: Repository, activity_id: int, day: date, name: str = "Run") -> None:
    repo.upsert_garmin_activity(
        activity_id=activity_id,
        day=day,
        name=name,
        type_key="running",
        duration_min=30,
        calories=300,
        distance_km=5.0,
    )


class TestGetGarminActivitiesForRange:
    """Tests for get_garmin_activities_range(start_date, end_date)."""

    def test_returns_activities_within_range(self, repo):
        d1 = date(2024, 1, 10)
        d2 = date(2024, 1, 12)
        d3 = date(2024, 1, 15)
        _seed_activity(repo, 1, d1, "Run 1")
        _seed_activity(repo, 2, d2, "Cycle")
        _seed_activity(repo, 3, d3, "Run 2")

        result = repo.get_garmin_activities_range(d1, d2)

        assert len(result) == 2
        dates = [r.date for r in result]
        assert d1 in dates
        assert d2 in dates
        assert d3 not in dates

    def test_excludes_activities_outside_range(self, repo):
        before = date(2024, 1, 9)
        after = date(2024, 1, 16)
        inside = date(2024, 1, 12)
        _seed_activity(repo, 10, before, "Before")
        _seed_activity(repo, 11, inside, "Inside")
        _seed_activity(repo, 12, after, "After")

        result = repo.get_garmin_activities_range(date(2024, 1, 10), date(2024, 1, 15))

        assert len(result) == 1
        assert result[0].name == "Inside"

    def test_inclusive_boundary_dates(self, repo):
        start = date(2024, 2, 1)
        end = date(2024, 2, 5)
        _seed_activity(repo, 20, start, "Start day")
        _seed_activity(repo, 21, end, "End day")

        result = repo.get_garmin_activities_range(start, end)

        assert len(result) == 2

    def test_empty_range_returns_empty_list(self, repo):
        result = repo.get_garmin_activities_range(date(2024, 3, 1), date(2024, 3, 7))
        assert result == []

    def test_ordering_by_date_then_activity_id(self, repo):
        """Two activities on same date with ids inserted out of order — assert id-sorted output."""
        day = date(2024, 4, 1)
        # Insert higher id first
        _seed_activity(repo, 999, day, "Second by id")
        _seed_activity(repo, 100, day, "First by id")
        _seed_activity(repo, 500, day, "Third by id")

        result = repo.get_garmin_activities_range(day, day)

        assert len(result) == 3
        ids = [r.garmin_activity_id for r in result]
        assert ids == [100, 500, 999]

    def test_multi_day_ordering(self, repo):
        """Activities across multiple days should be date-ascending, then id-ascending."""
        d1 = date(2024, 5, 1)
        d2 = date(2024, 5, 2)
        _seed_activity(repo, 300, d2, "Day2 Act")
        _seed_activity(repo, 200, d1, "Day1 Act")

        result = repo.get_garmin_activities_range(d1, d2)

        assert result[0].date == d1
        assert result[1].date == d2
