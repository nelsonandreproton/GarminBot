"""Tests for blood pressure display in /sync, /hoje, /ontem (format_daily_summary)."""

from datetime import date


def test_format_daily_summary_shows_blood_pressure():
    from src.telegram.formatters import format_daily_summary
    metrics = {
        "date": date(2026, 8, 21),
        "steps": 10000,
        "blood_pressure_systolic": 118,
        "blood_pressure_diastolic": 72,
        "blood_pressure_pulse": 60,
    }
    text = format_daily_summary(metrics)
    assert "Tensão arterial" in text
    assert "118/72" in text
    assert "60 bpm" in text


def test_format_daily_summary_blood_pressure_without_pulse():
    from src.telegram.formatters import format_daily_summary
    metrics = {
        "date": date(2026, 8, 21),
        "steps": 10000,
        "blood_pressure_systolic": 118,
        "blood_pressure_diastolic": 72,
    }
    text = format_daily_summary(metrics)
    assert "118/72" in text
    assert "bpm" not in text


def test_format_daily_summary_no_blood_pressure_not_shown():
    from src.telegram.formatters import format_daily_summary
    metrics = {
        "date": date(2026, 8, 21),
        "steps": 10000,
    }
    text = format_daily_summary(metrics)
    assert "Tensão arterial" not in text
