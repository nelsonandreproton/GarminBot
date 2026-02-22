"""Tests for _parse_date_prefix in src/telegram/bot.py."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.telegram.bot import _parse_date_prefix


TODAY = date.today()


def test_no_args_returns_today():
    d, remaining = _parse_date_prefix([])
    assert d == TODAY
    assert remaining == []


def test_plain_text_unchanged():
    args = ["2", "ovos"]
    d, remaining = _parse_date_prefix(args)
    assert d == TODAY
    assert remaining == args


def test_ontem():
    d, remaining = _parse_date_prefix(["ontem", "1", "banana"])
    assert d == TODAY - timedelta(days=1)
    assert remaining == ["1", "banana"]


def test_ontem_case_insensitive():
    d, remaining = _parse_date_prefix(["Ontem"])
    assert d == TODAY - timedelta(days=1)
    assert remaining == []


def test_anteontem():
    d, remaining = _parse_date_prefix(["anteontem", "ovo"])
    assert d == TODAY - timedelta(days=2)
    assert remaining == ["ovo"]


def test_iso_date():
    target = TODAY - timedelta(days=5)
    d, remaining = _parse_date_prefix([target.isoformat(), "frango"])
    assert d == target
    assert remaining == ["frango"]


def test_pt_date_format():
    target = TODAY - timedelta(days=3)
    pt_str = target.strftime("%d/%m/%Y")
    d, remaining = _parse_date_prefix([pt_str, "arroz"])
    assert d == target
    assert remaining == ["arroz"]


def test_future_date_raises():
    future = (TODAY + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="futuras"):
        _parse_date_prefix([future])


def test_future_pt_date_raises():
    future = (TODAY + timedelta(days=1)).strftime("%d/%m/%Y")
    with pytest.raises(ValueError, match="futuras"):
        _parse_date_prefix([future])


def test_invalid_iso_raises():
    with pytest.raises(ValueError, match="inválida"):
        _parse_date_prefix(["2024-99-99"])


def test_invalid_pt_raises():
    with pytest.raises(ValueError, match="inválida"):
        _parse_date_prefix(["99/99/2024"])


def test_today_no_prefix():
    """A word that looks like a date keyword but isn't should be left in args."""
    args = ["amanha", "comer"]  # not a recognised keyword
    d, remaining = _parse_date_prefix(args)
    assert d == TODAY
    assert remaining == args
