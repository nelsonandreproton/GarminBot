"""Tests for src/utils/insights.py."""

from datetime import date, timedelta
from unittest.mock import MagicMock

from src.utils.insights import generate_insights, generate_daily_alerts, _count_streak


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


class TestGenerateDailyAlerts:
    """/hoje reports a still-accumulating today; the sedentary-day alert must not
    fire on that partial data (bug: it showed on /hoje with only a few hundred
    steps logged so far, mislabeled as "ontem" / yesterday)."""

    def test_low_steps_completed_day_triggers_alert(self):
        alerts = generate_daily_alerts({"steps": 500}, [], is_live_day=False)
        assert any("parado" in a for a in alerts)

    def test_low_steps_live_day_does_not_trigger_alert(self):
        alerts = generate_daily_alerts({"steps": 500}, [], is_live_day=True)
        assert not any("parado" in a for a in alerts)

    def test_low_steps_default_is_completed_day(self):
        """is_live_day defaults to False — /ontem and the scheduled morning report
        don't pass it and must keep the existing sedentary-day alert behavior."""
        alerts = generate_daily_alerts({"steps": 500}, [])
        assert any("parado" in a for a in alerts)

    def test_high_steps_live_day_no_alert_either_way(self):
        alerts = generate_daily_alerts({"steps": 9000}, [], is_live_day=True)
        assert not any("parado" in a for a in alerts)

    def test_sleep_alert_unaffected_by_is_live_day(self):
        """Sleep alerts reflect last night's completed sleep even on /hoje — must
        still fire regardless of is_live_day."""
        alerts = generate_daily_alerts({"sleep_hours": 4.5}, [], is_live_day=True)
        assert any("Dormiste pouco" in a for a in alerts)
