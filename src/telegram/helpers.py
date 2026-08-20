"""Shared helpers used by TelegramBot and command mixins."""

from __future__ import annotations

import functools
import logging
import time
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Rate limiting: max 1 command per N seconds per chat
_RATE_LIMIT_SECONDS = 3
_last_command_time: dict[int, float] = {}


def _is_rate_limited(chat_id: int) -> bool:
    now = time.monotonic()
    last = _last_command_time.get(chat_id, 0.0)
    if now - last < _RATE_LIMIT_SECONDS:
        return True
    _last_command_time[chat_id] = now
    return False


def _parse_date_prefix(args: list[str]) -> tuple[date, list[str]]:
    """Parse an optional date prefix from command args.

    Recognised prefixes (case-insensitive):
      ontem          → yesterday
      anteontem      → two days ago
      YYYY-MM-DD     → exact date
      DD/MM/YYYY     → exact date (pt format)

    Returns (resolved_date, remaining_args).
    Raises ValueError if a date keyword is recognised but the date is invalid or in the future.
    """
    today = date.today()
    if not args:
        return today, args

    first = args[0].lower()

    if first == "ontem":
        return today - timedelta(days=1), args[1:]

    if first == "anteontem":
        return today - timedelta(days=2), args[1:]

    # Try YYYY-MM-DD
    if len(first) == 10 and first[4] == "-" and first[7] == "-":
        try:
            parsed = date.fromisoformat(args[0])
        except ValueError:
            raise ValueError(f"Data inválida: {args[0]}")
        if parsed > today:
            raise ValueError("Não posso registar em datas futuras.")
        return parsed, args[1:]

    # Try DD/MM/YYYY
    if len(first) == 10 and first[2] == "/" and first[5] == "/":
        try:
            day, month, year = first.split("/")
            parsed = date(int(year), int(month), int(day))
        except (ValueError, IndexError):
            raise ValueError(f"Data inválida: {args[0]}")
        if parsed > today:
            raise ValueError("Não posso registar em datas futuras.")
        return parsed, args[1:]

    return today, args


_EXPORT_FORMATS = {"csv", "json", "xlsx"}


def _parse_export_args(args: list[str]) -> tuple[str, date, date]:
    """Parse `[formato] [inicio] [fim]` args for /exportar's subcommands
    (base metrics, refeicoes, treinos).

    formato ∈ {csv, json, xlsx} (case-insensitive, default csv).
    inicio/fim in YYYY-MM-DD format. Missing inicio defaults to 90 days ago;
    missing fim defaults to today.

    Returns (formato, start_date, end_date). Raises ValueError on invalid input.
    """
    today = date.today()
    tokens = list(args)

    fmt = "csv"
    if tokens and tokens[0].lower() in _EXPORT_FORMATS:
        fmt = tokens[0].lower()
        tokens = tokens[1:]

    def _parse_date(token: str) -> date:
        try:
            return date.fromisoformat(token)
        except ValueError:
            raise ValueError(f"Data inválida: {token}. Usa o formato YYYY-MM-DD.")

    if not tokens:
        return fmt, today - timedelta(days=90), today
    if len(tokens) == 1:
        start, end = _parse_date(tokens[0]), today
    elif len(tokens) == 2:
        start, end = _parse_date(tokens[0]), _parse_date(tokens[1])
    else:
        raise ValueError("Demasiados argumentos. Usa: [formato] [inicio] [fim]")

    if start > today:
        raise ValueError("Não posso exportar datas futuras.")
    if end > today:
        end = today
    if start > end:
        raise ValueError("A data de início deve ser anterior à data de fim.")

    return fmt, start, end


def safe_command(func):
    """Decorator that catches unhandled exceptions in command handlers.

    Logs the error and sends a generic error message to the user so the bot
    never silently dies. Does NOT interfere with explicit try/except blocks
    inside the handler.

    Usage::

        @safe_command
        async def _cmd_foo(self, update, context):
            ...
    """
    @functools.wraps(func)
    async def wrapper(self, update, context, *args, **kwargs):
        try:
            return await func(self, update, context, *args, **kwargs)
        except Exception as exc:
            logger.error("Unhandled error in %s: %s", func.__name__, exc, exc_info=True)
            try:
                if update and update.message:
                    await update.message.reply_text("❌ Ocorreu um erro inesperado. Tenta novamente.")
            except Exception:
                pass
    return wrapper


def _row_to_metrics(row: Any) -> dict[str, Any]:
    """Convert a DailyMetrics ORM row to a flat dict for formatters."""
    return {
        "date": row.date,
        "sleep_hours": row.sleep_hours,
        "sleep_score": row.sleep_score,
        "sleep_quality": row.sleep_quality,
        "sleep_deep_min": getattr(row, "sleep_deep_min", None),
        "sleep_light_min": getattr(row, "sleep_light_min", None),
        "sleep_rem_min": getattr(row, "sleep_rem_min", None),
        "sleep_awake_min": getattr(row, "sleep_awake_min", None),
        "steps": row.steps,
        "active_calories": row.active_calories,
        "resting_calories": row.resting_calories,
        "total_calories": row.total_calories,
        "floors_ascended": getattr(row, "floors_ascended", None),
        "intensity_moderate_min": getattr(row, "intensity_moderate_min", None),
        "intensity_vigorous_min": getattr(row, "intensity_vigorous_min", None),
        "resting_heart_rate": row.resting_heart_rate,
        "avg_stress": row.avg_stress,
        "body_battery_high": row.body_battery_high,
        "body_battery_low": row.body_battery_low,
        "spo2_avg": getattr(row, "spo2_avg", None),
        "weight_kg": row.weight_kg,
    }
