"""GarminBot MCP server — Phase 1: read-only tools over local stdio.

Run with:
    python -m src.mcp.server

Or via MCP client config:
    {"command": "python", "args": ["-m", "src.mcp.server"], "cwd": "/path/to/GarminBot"}
"""

from __future__ import annotations

import os
import pathlib
from datetime import date

from mcp.server.fastmcp import FastMCP

from src.database.repository import Repository
from src.mcp import tools

# Maximum number of days allowed for weight trend queries (10 years).
_MAX_TREND_DAYS = 3650


def _parse_date(value: str, param_name: str = "date") -> date:
    """Parse an ISO date string, raising ValueError with a clear message on failure."""
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError(
            f"Invalid {param_name} format: {value!r}. Expected ISO 8601, e.g. '2024-06-01'."
        )


def build_server(repo: Repository) -> FastMCP:
    """Build and return a FastMCP instance with all tools registered.

    Accepts an existing Repository so tests can inject a test DB without
    any filesystem side effects at import time.
    """
    mcp = FastMCP("garminbot")

    @mcp.tool()
    def get_daily_metrics(day: str | None = None) -> dict | None:
        """Return Garmin health metrics for a single day (ISO date string, e.g. '2024-06-01').

        Defaults to today. Returns None if no data exists for that day.
        Includes sleep, steps, calories, heart rate, stress, body battery, SpO2, weight.
        """
        parsed = _parse_date(day, "day") if day else None
        return tools.get_daily_metrics(repo, parsed)

    @mcp.tool()
    def get_metrics_range(start: str, end: str) -> list[dict]:
        """Return Garmin health metrics for all days in [start, end] (ISO date strings).

        Returns a list ordered by date ascending. Empty list if no data.
        """
        start_d = _parse_date(start, "start")
        end_d = _parse_date(end, "end")
        if start_d > end_d:
            raise ValueError(f"start ({start}) must be <= end ({end}).")
        return tools.get_metrics_range(repo, start_d, end_d)

    @mcp.tool()
    def get_weekly_stats(end_date: str | None = None) -> dict:
        """Return 7-day aggregate health stats ending on end_date (ISO date, defaults to today).

        Includes average/best/worst sleep, average steps, total calories.
        Empty dict if no data in that week.
        """
        parsed = _parse_date(end_date, "end_date") if end_date else None
        return tools.get_weekly_stats(repo, parsed)

    @mcp.tool()
    def get_monthly_stats(end_date: str | None = None) -> dict:
        """Return 30-day aggregate health stats ending on end_date (ISO date, defaults to today).

        Includes average sleep hours, average and total steps, total active calories.
        Empty dict if no data in that month.
        """
        parsed = _parse_date(end_date, "end_date") if end_date else None
        return tools.get_monthly_stats(repo, parsed)

    @mcp.tool()
    def get_weight_trend(days: int = 90) -> dict:
        """Return weight trend for the last N days (default 90, max 3650).

        Returns {"records": [...], "stats": {...}} where records is oldest-first
        list of {date, weight_kg} and stats has current/previous week comparison.
        """
        if not (1 <= days <= _MAX_TREND_DAYS):
            raise ValueError(f"days must be between 1 and {_MAX_TREND_DAYS}, got {days}.")
        return tools.get_weight_trend(repo, days)

    @mcp.tool()
    def get_nutrition(day: str | None = None) -> dict:
        """Return nutrition data for a day (ISO date, defaults to today).

        Returns {"totals": {calories, protein_g, fat_g, carbs_g, fiber_g, entry_count},
                 "entries": [list of individual food entries]}.
        Totals are zero-defaulted when no food was logged.
        """
        parsed = _parse_date(day, "day") if day else None
        return tools.get_nutrition(repo, parsed)

    @mcp.tool()
    def get_nutrition_trend(end_date: str | None = None) -> dict:
        """Return 7-day average nutrition figures ending on end_date (ISO date, defaults to today).

        Returns avg_calories, avg_protein, avg_fat, avg_carbs, avg_fiber, days_with_data.
        """
        parsed = _parse_date(end_date, "end_date") if end_date else None
        return tools.get_nutrition_trend(repo, parsed)

    @mcp.tool()
    def get_training_load(end_date: str | None = None) -> dict:
        """Return weekly training load by activity type plus recent manual training log entries.

        Returns {"by_type": {type_key: {minutes, km, count}}, "recent": [training entries]}.
        end_date is used only by by_type (7-day Garmin activity window ending on end_date,
        defaults to today). recent is ALWAYS the 7 calendar days ending today, regardless
        of end_date — it queries TrainingEntry rows with date > today-7.
        """
        parsed = _parse_date(end_date, "end_date") if end_date else None
        return tools.get_training_load(repo, parsed)

    @mcp.tool()
    def get_activities(start: str, end: str) -> list[dict]:
        """Return Garmin auto-synced activities in [start, end] (ISO date strings).

        Ordered by date ascending, then activity id. Each entry has garmin_activity_id,
        name, type_key, duration_min, calories, distance_km.
        """
        start_d = _parse_date(start, "start")
        end_d = _parse_date(end, "end")
        if start_d > end_d:
            raise ValueError(f"start ({start}) must be <= end ({end}).")
        return tools.get_activities(repo, start_d, end_d)

    @mcp.tool()
    def get_goals() -> dict:
        """Return all user-defined health goals as {metric: target_value}.

        Defaults: steps=10000, sleep_hours=7.0. Custom goals override defaults.
        """
        return tools.get_goals(repo)

    @mcp.tool()
    def get_deficit(day: str | None = None) -> dict:
        """Return caloric deficit/surplus for a day (ISO date, defaults to today).

        Returns {date, deficit_kcal, deficit_pct, burned, eaten}.
        Positive deficit_kcal means ate less than burned (caloric deficit).
        Negative means surplus. Values are None when data is insufficient.
        """
        parsed = _parse_date(day, "day") if day else None
        return tools.get_deficit(repo, parsed)

    return mcp


if __name__ == "__main__":
    db_path = os.environ.get("DATABASE_PATH", "./data/garmin_data.db")
    # Resolve to an absolute canonical path so the location is unambiguous.
    db_path = str(pathlib.Path(db_path).resolve())
    _repo = Repository(db_path, read_only=True)
    mcp = build_server(_repo)
    mcp.run(transport="stdio")
