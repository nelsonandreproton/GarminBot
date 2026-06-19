"""Tests for input validation in MCP server wrappers (server.py).

Covers:
- _parse_date: bad format raises ValueError with descriptive message
- get_weight_trend: days bounds check
- get_metrics_range / get_activities: start > end rejected

When validation errors occur inside a FastMCP tool, FastMCP wraps the
underlying ValueError in a ToolError. Tests that exercise the tool via
server.call_tool() must therefore catch ToolError, not ValueError directly.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from src.database.repository import Repository
from src.mcp.server import _parse_date, build_server


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("server_val") / "test.db")
    r = Repository(db_path)
    r.init_database()
    mcp = build_server(r)
    yield mcp
    r._engine.dispose()


# ---------------------------------------------------------------------------
# _parse_date helper (pure Python — no MCP wrapping, raises ValueError directly)
# ---------------------------------------------------------------------------

class TestParseDateHelper:
    def test_valid_iso_date_returns_date_object(self):
        result = _parse_date("2024-06-01")
        assert result == date(2024, 6, 1)

    def test_invalid_format_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid.*format"):
            _parse_date("not-a-date")

    def test_ambiguous_format_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid"):
            _parse_date("06/01/2024")

    def test_out_of_range_date_raises_value_error(self):
        with pytest.raises(ValueError):
            _parse_date("2024-99-99")

    def test_param_name_appears_in_error_message(self):
        with pytest.raises(ValueError, match="start"):
            _parse_date("bad", param_name="start")

    def test_expected_format_hint_in_error(self):
        with pytest.raises(ValueError, match="2024-06-01"):
            _parse_date("06-01-2024", param_name="day")


# ---------------------------------------------------------------------------
# get_weight_trend bounds check
# FastMCP wraps ValueError → ToolError, so we catch ToolError here.
# ---------------------------------------------------------------------------

class TestWeightTrendBoundsCheck:
    @pytest.mark.asyncio
    async def test_zero_days_raises(self, server):
        with pytest.raises(ToolError, match="days must be between"):
            await server.call_tool("get_weight_trend", {"days": 0})

    @pytest.mark.asyncio
    async def test_negative_days_raises(self, server):
        with pytest.raises(ToolError, match="days must be between"):
            await server.call_tool("get_weight_trend", {"days": -1})

    @pytest.mark.asyncio
    async def test_exceeds_max_raises(self, server):
        with pytest.raises(ToolError, match="days must be between"):
            await server.call_tool("get_weight_trend", {"days": 9999999})

    @pytest.mark.asyncio
    async def test_boundary_1_valid(self, server):
        # days=1 is the minimum valid value — should not raise
        result = await server.call_tool("get_weight_trend", {"days": 1})
        assert result is not None

    @pytest.mark.asyncio
    async def test_boundary_3650_valid(self, server):
        result = await server.call_tool("get_weight_trend", {"days": 3650})
        assert result is not None


# ---------------------------------------------------------------------------
# get_metrics_range: start > end
# ---------------------------------------------------------------------------

class TestMetricsRangeDateOrder:
    @pytest.mark.asyncio
    async def test_start_after_end_raises(self, server):
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        with pytest.raises(ToolError, match="must be <="):
            await server.call_tool("get_metrics_range", {"start": today, "end": yesterday})

    @pytest.mark.asyncio
    async def test_same_start_and_end_valid(self, server):
        today = date.today().isoformat()
        result = await server.call_tool("get_metrics_range", {"start": today, "end": today})
        assert result is not None

    @pytest.mark.asyncio
    async def test_bad_start_date_raises(self, server):
        with pytest.raises(ToolError, match="Invalid.*start"):
            await server.call_tool(
                "get_metrics_range",
                {"start": "not-a-date", "end": date.today().isoformat()},
            )


# ---------------------------------------------------------------------------
# get_activities: start > end
# ---------------------------------------------------------------------------

class TestActivitiesDateOrder:
    @pytest.mark.asyncio
    async def test_start_after_end_raises(self, server):
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        with pytest.raises(ToolError, match="must be <="):
            await server.call_tool("get_activities", {"start": today, "end": yesterday})

    @pytest.mark.asyncio
    async def test_bad_end_date_raises(self, server):
        with pytest.raises(ToolError, match="Invalid.*end"):
            await server.call_tool(
                "get_activities",
                {"start": date.today().isoformat(), "end": "2024/99/99"},
            )

    @pytest.mark.asyncio
    async def test_valid_range_accepted(self, server):
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        result = await server.call_tool("get_activities", {"start": yesterday, "end": today})
        assert result is not None


# ---------------------------------------------------------------------------
# get_metrics_range: date-range cap (_MAX_RANGE_DAYS = 3650)
# ---------------------------------------------------------------------------

class TestMetricsRangeCap:
    @pytest.mark.asyncio
    async def test_range_exceeding_max_days_raises(self, server):
        # 2000-01-01 → 2020-01-01 is ~7305 days — well over the 3650-day cap.
        with pytest.raises(ToolError, match="exceeds maximum"):
            await server.call_tool(
                "get_metrics_range",
                {"start": "2000-01-01", "end": "2020-01-01"},
            )

    @pytest.mark.asyncio
    async def test_range_exactly_at_max_days_accepted(self, server):
        # Exactly 3650 days apart — should NOT raise (boundary is inclusive).
        start = date(2014, 6, 19)
        end = start + timedelta(days=3650)
        result = await server.call_tool(
            "get_metrics_range",
            {"start": start.isoformat(), "end": end.isoformat()},
        )
        assert result is not None


# ---------------------------------------------------------------------------
# get_activities: date-range cap (_MAX_RANGE_DAYS = 3650)
# ---------------------------------------------------------------------------

class TestActivitiesRangeCap:
    @pytest.mark.asyncio
    async def test_range_exceeding_max_days_raises(self, server):
        # 2000-01-01 → 2020-01-01 is ~7305 days — well over the 3650-day cap.
        with pytest.raises(ToolError, match="exceeds maximum"):
            await server.call_tool(
                "get_activities",
                {"start": "2000-01-01", "end": "2020-01-01"},
            )

    @pytest.mark.asyncio
    async def test_range_exactly_at_max_days_accepted(self, server):
        # Exactly 3650 days apart — should NOT raise (boundary is inclusive).
        start = date(2014, 6, 19)
        end = start + timedelta(days=3650)
        result = await server.call_tool(
            "get_activities",
            {"start": start.isoformat(), "end": end.isoformat()},
        )
        assert result is not None
