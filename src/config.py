"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass
class Config:
    garmin_email: str
    garmin_password: str
    telegram_bot_token: str
    telegram_chat_id: str
    database_path: str
    daily_sync_time: str
    daily_report_time: str
    weekly_report_day: str
    weekly_report_time: str
    timezone: str
    log_level: str
    log_file: str
    sync_retry_delay_minutes: int
    health_port: int | None
    daily_alerts: bool
    groq_api_key: str | None

    # Derived fields
    sync_hour: int = field(init=False)
    sync_minute: int = field(init=False)
    report_hour: int = field(init=False)
    report_minute: int = field(init=False)
    weekly_hour: int = field(init=False)
    weekly_minute: int = field(init=False)

    def __post_init__(self) -> None:
        self.sync_hour, self.sync_minute = self._parse_time(self.daily_sync_time, "DAILY_SYNC_TIME")
        self.report_hour, self.report_minute = self._parse_time(self.daily_report_time, "DAILY_REPORT_TIME")
        self.weekly_hour, self.weekly_minute = self._parse_time(self.weekly_report_time, "WEEKLY_REPORT_TIME")

    @staticmethod
    def _parse_time(value: str, name: str) -> tuple[int, int]:
        """Parse HH:MM string into (hour, minute) tuple."""
        try:
            parts = value.strip().split(":")
            return int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            raise ConfigError(f"{name} must be in HH:MM format, got: {value!r}")


def load_config() -> Config:
    """Load and validate configuration from environment variables."""
    required = {
        "GARMIN_EMAIL": os.getenv("GARMIN_EMAIL"),
        "GARMIN_PASSWORD": os.getenv("GARMIN_PASSWORD"),
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN"),
        "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID"),
    }

    missing = [k for k, v in required.items() if not v]
    if missing:
        raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")

    # Ensure data and logs directories exist
    database_path = os.getenv("DATABASE_PATH", "./data/garmin_data.db")
    log_file = os.getenv("LOG_FILE", "./logs/bot.log")

    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    # HEALTH_PORT: optional int
    health_port_raw = os.getenv("HEALTH_PORT")
    health_port: int | None = None
    if health_port_raw is not None:
        try:
            health_port = int(health_port_raw)
        except ValueError:
            raise ConfigError(f"HEALTH_PORT must be an integer, got: {health_port_raw!r}")

    # DAILY_ALERTS: default True, False only if value is "false"
    daily_alerts_raw = os.getenv("DAILY_ALERTS", "true")
    daily_alerts = daily_alerts_raw.strip().lower() != "false"

    # SYNC_RETRY_DELAY_MINUTES: default 30
    sync_retry_delay_minutes = int(os.getenv("SYNC_RETRY_DELAY_MINUTES", "30"))

    # GROQ_API_KEY: optional — nutrition features disabled if absent
    groq_api_key = os.getenv("GROQ_API_KEY") or None

    return Config(
        garmin_email=required["GARMIN_EMAIL"],  # type: ignore[arg-type]
        garmin_password=required["GARMIN_PASSWORD"],  # type: ignore[arg-type]
        telegram_bot_token=required["TELEGRAM_BOT_TOKEN"],  # type: ignore[arg-type]
        telegram_chat_id=required["TELEGRAM_CHAT_ID"],  # type: ignore[arg-type]
        database_path=database_path,
        daily_sync_time=os.getenv("DAILY_SYNC_TIME", "07:00"),
        daily_report_time=os.getenv("DAILY_REPORT_TIME", "08:00"),
        weekly_report_day=os.getenv("WEEKLY_REPORT_DAY", "sunday"),
        weekly_report_time=os.getenv("WEEKLY_REPORT_TIME", "20:00"),
        timezone=os.getenv("TIMEZONE", "Europe/Lisbon"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        log_file=log_file,
        sync_retry_delay_minutes=sync_retry_delay_minutes,
        health_port=health_port,
        daily_alerts=daily_alerts,
        groq_api_key=groq_api_key,
    )
