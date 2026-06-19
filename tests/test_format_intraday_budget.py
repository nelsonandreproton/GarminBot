"""Tests for format_budget_line formatter: single Orçamento line for /hoje."""

from __future__ import annotations

import pytest

from src.telegram.formatters import format_budget_line


# ---------------------------------------------------------------------------
# Basic correctness
# ---------------------------------------------------------------------------

class TestFormatBudgetLine:
    def test_returns_orcamento_line_for_valid_burn(self):
        result = format_budget_line(2600)
        assert result is not None
        assert "Orçamento" in result

    def test_budget_uses_round_not_int_truncation(self):
        """round(0.70 * 2600) == 1820; int(0.70 * 2600) == 1819 due to float imprecision."""
        result = format_budget_line(2600)
        assert result is not None
        assert "1820" in result
        assert "1819" not in result

    def test_budget_70_percent_of_3000(self):
        """0.70 * 3000 = 2100 exactly."""
        result = format_budget_line(3000)
        assert result is not None
        assert "2100" in result

    def test_default_label_says_30_percent(self):
        result = format_budget_line(2600)
        assert result is not None
        assert "30%" in result

    def test_custom_deficit_pct_20(self):
        """20% deficit → 80% budget. 0.80 * 2000 = 1600."""
        result = format_budget_line(2000, deficit_pct=0.20)
        assert result is not None
        assert "1600" in result
        assert "20%" in result

    def test_custom_deficit_pct_label(self):
        result = format_budget_line(2000, deficit_pct=0.20)
        assert result is not None
        assert "20% défice" in result

    def test_returns_none_when_total_burned_is_none(self):
        result = format_budget_line(None)
        assert result is None

    def test_returns_none_when_total_burned_is_zero(self):
        result = format_budget_line(0)
        assert result is None

    def test_returns_none_when_total_burned_is_negative(self):
        result = format_budget_line(-100)
        assert result is None

    def test_does_not_contain_gasto(self):
        result = format_budget_line(2600)
        assert result is not None
        assert "Gasto" not in result

    def test_does_not_contain_comido(self):
        result = format_budget_line(2600)
        assert result is not None
        assert "Comido" not in result

    def test_single_line_output(self):
        """Must return exactly one non-empty line."""
        result = format_budget_line(2600)
        assert result is not None
        lines = [l for l in result.splitlines() if l.strip()]
        assert len(lines) == 1

    def test_contains_kcal_unit(self):
        result = format_budget_line(2600)
        assert result is not None
        assert "kcal" in result
