"""Tests for format_intraday_budget formatter (Phase 4: raw intraday deficit control)."""

from __future__ import annotations

import pytest

from src.telegram.formatters import format_intraday_budget


# ---------------------------------------------------------------------------
# Full block: burn + eaten
# ---------------------------------------------------------------------------

class TestFormatIntradayBudgetFullBlock:
    def test_full_block_contains_gasto(self):
        eaten = {"calories": 1400, "protein_g": 100, "fat_g": 40, "carbs_g": 120}
        result = format_intraday_budget(eaten, total_burned=2600)
        assert result is not None
        assert "Gasto" in result
        assert "2600" in result

    def test_full_block_contains_comido(self):
        eaten = {"calories": 1400, "protein_g": 100, "fat_g": 40, "carbs_g": 120}
        result = format_intraday_budget(eaten, total_burned=2600)
        assert "Comido" in result
        assert "1400" in result

    def test_full_block_contains_orcamento(self):
        eaten = {"calories": 1400, "protein_g": 100, "fat_g": 40, "carbs_g": 120}
        result = format_intraday_budget(eaten, total_burned=2600)
        assert "Orçamento" in result

    def test_budget_uses_round_not_int_truncation(self):
        """round(0.70 * 2600) == 1820; int(0.70 * 2600) == 1819 due to float imprecision."""
        eaten = {"calories": 1400, "protein_g": 100, "fat_g": 40, "carbs_g": 120}
        result = format_intraday_budget(eaten, total_burned=2600)
        # Must show 1820, NOT 1819
        assert "1820" in result
        assert "1819" not in result

    def test_budget_is_70_percent_of_burn(self):
        """Budget = (1 - 0.30) * burn. Verified for a round number."""
        eaten = {"calories": 2000, "protein_g": 150, "fat_g": 60, "carbs_g": 200}
        result = format_intraday_budget(eaten, total_burned=3000)
        # 0.70 * 3000 = 2100
        assert "2100" in result

    def test_macros_protein_shown(self):
        eaten = {"calories": 1400, "protein_g": 100, "fat_g": 40, "carbs_g": 120}
        result = format_intraday_budget(eaten, total_burned=2600)
        assert "100g" in result or "P: 100" in result or "P:100" in result

    def test_macros_fat_shown(self):
        eaten = {"calories": 1400, "protein_g": 100, "fat_g": 40, "carbs_g": 120}
        result = format_intraday_budget(eaten, total_burned=2600)
        assert "40g" in result or "G: 40" in result or "G:40" in result

    def test_macros_carbs_shown(self):
        eaten = {"calories": 1400, "protein_g": 100, "fat_g": 40, "carbs_g": 120}
        result = format_intraday_budget(eaten, total_burned=2600)
        assert "120g" in result or "HC: 120" in result or "HC:120" in result

    def test_macros_rendered_as_integers_with_g(self):
        """Macros must be integer values with 'g' suffix — no decimals."""
        eaten = {"calories": 1400.5, "protein_g": 100.7, "fat_g": 40.2, "carbs_g": 120.9}
        result = format_intraday_budget(eaten, total_burned=2600)
        # No decimal points in macro values
        assert "100.7g" not in result
        assert "40.2g" not in result
        assert "120.9g" not in result

    def test_three_line_structure(self):
        """Expect exactly three non-empty lines in the block."""
        eaten = {"calories": 1400, "protein_g": 100, "fat_g": 40, "carbs_g": 120}
        result = format_intraday_budget(eaten, total_burned=2600)
        lines = [l for l in result.splitlines() if l.strip()]
        assert len(lines) == 3

    def test_custom_deficit_pct(self):
        """A 20% deficit means 80% budget."""
        eaten = {"calories": 1600, "protein_g": 120, "fat_g": 50, "carbs_g": 180}
        result = format_intraday_budget(eaten, total_burned=2000, deficit_pct=0.20)
        # 0.80 * 2000 = 1600
        assert "1600" in result
        assert "20% défice" in result or "20%" in result


# ---------------------------------------------------------------------------
# Eaten-only: burn is None or 0
# ---------------------------------------------------------------------------

class TestFormatIntradayBudgetEatenOnly:
    def test_none_burn_returns_comido_line(self):
        eaten = {"calories": 1200, "protein_g": 80, "fat_g": 35, "carbs_g": 150}
        result = format_intraday_budget(eaten, total_burned=None)
        assert result is not None
        assert "Comido" in result
        assert "1200" in result

    def test_none_burn_no_gasto_line(self):
        eaten = {"calories": 1200, "protein_g": 80, "fat_g": 35, "carbs_g": 150}
        result = format_intraday_budget(eaten, total_burned=None)
        assert "Gasto" not in result

    def test_none_burn_no_orcamento_line(self):
        eaten = {"calories": 1200, "protein_g": 80, "fat_g": 35, "carbs_g": 150}
        result = format_intraday_budget(eaten, total_burned=None)
        assert "Orçamento" not in result

    def test_zero_burn_returns_comido_line(self):
        eaten = {"calories": 900, "protein_g": 60, "fat_g": 20, "carbs_g": 100}
        result = format_intraday_budget(eaten, total_burned=0)
        assert result is not None
        assert "Comido" in result

    def test_zero_burn_no_orcamento_line(self):
        eaten = {"calories": 900, "protein_g": 60, "fat_g": 20, "carbs_g": 100}
        result = format_intraday_budget(eaten, total_burned=0)
        assert "Orçamento" not in result

    def test_eaten_only_block_is_single_line(self):
        """When burn is None, only one non-empty line (Comido)."""
        eaten = {"calories": 1000, "protein_g": 70, "fat_g": 30, "carbs_g": 130}
        result = format_intraday_budget(eaten, total_burned=None)
        lines = [l for l in result.splitlines() if l.strip()]
        assert len(lines) == 1


# ---------------------------------------------------------------------------
# Both empty: return None
# ---------------------------------------------------------------------------

class TestFormatIntradayBudgetNoneReturn:
    def test_none_when_both_empty(self):
        eaten = {"calories": None, "protein_g": None, "fat_g": None, "carbs_g": None}
        result = format_intraday_budget(eaten, total_burned=None)
        assert result is None

    def test_none_when_zero_calories_and_no_burn(self):
        eaten = {"calories": 0, "protein_g": 0, "fat_g": 0, "carbs_g": 0}
        result = format_intraday_budget(eaten, total_burned=None)
        assert result is None

    def test_none_when_empty_dict_and_no_burn(self):
        result = format_intraday_budget({}, total_burned=None)
        assert result is None

    def test_none_when_empty_dict_and_zero_burn(self):
        result = format_intraday_budget({}, total_burned=0)
        assert result is None

    def test_not_none_when_burn_present_but_no_eaten(self):
        """If burn is known but no food logged, still show Gasto + Orçamento."""
        result = format_intraday_budget({}, total_burned=2600)
        assert result is not None
        assert "Gasto" in result
        assert "Orçamento" in result


# ---------------------------------------------------------------------------
# NO forbidden content: "faltam", subtraction, percentage-of-deficit display
# ---------------------------------------------------------------------------

class TestFormatIntradayBudgetForbiddenContent:
    def test_no_faltam_text(self):
        eaten = {"calories": 1400, "protein_g": 100, "fat_g": 40, "carbs_g": 120}
        result = format_intraday_budget(eaten, total_burned=2600)
        assert "faltam" not in result.lower()

    def test_no_remaining_macros_text(self):
        eaten = {"calories": 1400, "protein_g": 100, "fat_g": 40, "carbs_g": 120}
        result = format_intraday_budget(eaten, total_burned=2600)
        # No "remaining" or "Faltam" in any case
        assert "Faltam" not in result
        assert "Remaining" not in result

    def test_no_surplus_deficit_percentage(self):
        """Must not show 'Excedente vs Garmin' or computed deficit % like '(46.2%)'."""
        eaten = {"calories": 1400, "protein_g": 100, "fat_g": 40, "carbs_g": 120}
        result = format_intraday_budget(eaten, total_burned=2600)
        # No computed balance/surplus lines
        assert "Excedente" not in result
        assert "vs Garmin" not in result
        # The only allowed "%" is the deficit label like "30% défice"
        # No decimal percentage like "46.2%" or "53.8%" (computed ratio)
        import re
        assert not re.search(r"\d+\.\d+%", result), "No decimal percentage allowed"

    def test_no_subtraction_result_displayed(self):
        """The difference (budget - eaten) must NOT appear."""
        eaten = {"calories": 1400, "protein_g": 100, "fat_g": 40, "carbs_g": 120}
        # budget = 1820, eaten = 1400, difference = 420
        result = format_intraday_budget(eaten, total_burned=2600)
        # "420" must not appear as a standalone calorie value (it's the difference)
        # We check that neither 420 kcal appears as "420 kcal" separately from other numbers
        # The only kcal values should be: 2600 (Gasto), 1400 (Comido), 1820 (Orçamento)
        assert "420 kcal" not in result
        assert "420kcal" not in result

    def test_does_not_call_format_remaining_macros(self):
        """format_intraday_budget is a completely separate formatter — no delegation."""
        from unittest.mock import patch
        with patch("src.telegram.formatters.format_remaining_macros") as mock_frm:
            eaten = {"calories": 1400, "protein_g": 100, "fat_g": 40, "carbs_g": 120}
            format_intraday_budget(eaten, total_burned=2600)
            mock_frm.assert_not_called()


# ---------------------------------------------------------------------------
# Exact rendered example: burn=2600, eaten=1400/100P/40G/120HC
# ---------------------------------------------------------------------------

class TestFormatIntradayBudgetRenderedExample:
    def test_rendered_example_values(self):
        """Regression: the exact example from the spec must render correctly."""
        eaten = {"calories": 1400, "protein_g": 100, "fat_g": 40, "carbs_g": 120}
        result = format_intraday_budget(eaten, total_burned=2600)
        assert result is not None
        # All three raw values present
        assert "2600" in result   # Gasto
        assert "1400" in result   # Comido
        assert "1820" in result   # Orçamento (round(0.70*2600))
        assert "100" in result    # Protein
        assert "40" in result     # Fat
        assert "120" in result    # Carbs

    def test_deficit_label_shows_30_percent(self):
        """The label must say '30% défice' (or '30%') for default deficit_pct=0.30."""
        eaten = {"calories": 1400, "protein_g": 100, "fat_g": 40, "carbs_g": 120}
        result = format_intraday_budget(eaten, total_burned=2600)
        assert "30%" in result
