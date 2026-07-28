"""Tests for format_budget_line formatter: 'Disponível'/'Excedido' line for /hoje."""

from __future__ import annotations

import pytest

from src.telegram.formatters import format_budget_line


# ---------------------------------------------------------------------------
# Basic correctness
# ---------------------------------------------------------------------------

class TestFormatBudgetLine:
    def test_returns_disponivel_line_when_under_budget(self):
        """total_burned=1083, eaten=375 -> budget=758, remaining=+383."""
        result = format_budget_line(1083, 375)
        assert result is not None
        assert "Disponível" in result
        assert "+383" in result

    def test_returns_excedido_line_when_over_budget(self):
        """total_burned=2000, eaten=1800 -> budget=1400, remaining=-400."""
        result = format_budget_line(2000, 1800)
        assert result is not None
        assert "Excedido" in result
        assert "-400" in result

    def test_budget_uses_round_not_int_truncation(self):
        """round(0.70 * 2600) == 1820; int(0.70 * 2600) == 1819 due to float imprecision."""
        result = format_budget_line(2600, 0)
        assert result is not None
        assert "+1.820" in result

    def test_custom_deficit_pct_20(self):
        """20% deficit -> 80% budget. 0.80 * 2000 = 1600."""
        result = format_budget_line(2000, 100, deficit_pct=0.20)
        assert result is not None
        assert "+1.500" in result

    def test_zero_eaten_shows_full_budget_available(self):
        result = format_budget_line(2600, 0)
        assert result is not None
        assert "+1.820" in result

    def test_exact_budget_match_shows_zero(self):
        """Eaten exactly equal to budget -> 0, treated as available (not exceeded)."""
        result = format_budget_line(2600, 1820)
        assert result is not None
        assert "+0" in result
        assert "Disponível" in result

    def test_returns_none_when_total_burned_is_none(self):
        result = format_budget_line(None, 375)
        assert result is None

    def test_returns_none_when_total_burned_is_zero(self):
        result = format_budget_line(0, 375)
        assert result is None

    def test_returns_none_when_total_burned_is_negative(self):
        result = format_budget_line(-100, 375)
        assert result is None

    def test_returns_none_when_eaten_cal_is_none(self):
        result = format_budget_line(2600, None)
        assert result is None

    def test_single_line_output(self):
        """Must return exactly one non-empty line."""
        result = format_budget_line(1083, 375)
        assert result is not None
        lines = [l for l in result.splitlines() if l.strip()]
        assert len(lines) == 1

    def test_contains_kcal_unit(self):
        result = format_budget_line(1083, 375)
        assert result is not None
        assert "kcal" in result

    def test_thousands_separator_on_excedido(self):
        """total_burned=1000, eaten=2500 -> budget=700, remaining=-1800 -> '-1.800'."""
        result = format_budget_line(1000, 2500)
        assert result is not None
        assert "Excedido" in result
        assert "-1.800" in result
