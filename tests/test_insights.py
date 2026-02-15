"""Tests for src/utils/insights.py."""

from datetime import date, timedelta
from unittest.mock import MagicMock

from src.utils.insights import generate_insights, _count_streak


def _make_row(day: date, steps: int | None = None, sleep_hours: float | None = None, weight_kg: float | None = None):
    row = MagicMock()
    row.date = day
    row.steps = steps
    row.sleep_hours = sleep_hours
    row.weight_kg = weight_kg
    return row


def test_no_rows_returns_empty():
    assert generate_insights([]) == []


def test_steps_streak_7_days():
    rows = [_make_row(date(2026, 2, 7) + timedelta(days=i), steps=11000) for i in range(7)]
    insights = generate_insights(rows)
    assert any("7 dias consecutivos" in i for i in insights)


def test_steps_streak_3_days():
    rows = [_make_row(date(2026, 2, 7) + timedelta(days=i), steps=500) for i in range(4)]
    rows[-1].steps = 11000
    rows[-2].steps = 11000
    rows[-3].steps = 11000
    insights = generate_insights(rows)
    assert any("3 dias consecutivos" in i for i in insights)


def test_below_sleep_goal_warning():
    rows = [_make_row(date(2026, 2, 7) + timedelta(days=i), sleep_hours=6.0) for i in range(7)]
    insights = generate_insights(rows)
    assert any("60%" in i for i in insights)


def test_count_streak():
    rows = [_make_row(date(2026, 2, 7) + timedelta(days=i), steps=11000) for i in range(5)]
    rows[1].steps = 500  # break
    streak = _count_streak(rows, lambda r: r.steps and r.steps >= 10000)
    assert streak == 3  # last 3 from end
