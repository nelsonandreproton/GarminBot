"""SQLAlchemy ORM models for the Garmin data database."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class DailyMetrics(Base):
    """Stores daily Garmin health metrics."""

    __tablename__ = "daily_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, unique=True, nullable=False, index=True)
    sleep_hours = Column(Float, nullable=True)
    sleep_score = Column(Integer, nullable=True)
    sleep_quality = Column(String(20), nullable=True)
    steps = Column(Integer, nullable=True)
    active_calories = Column(Integer, nullable=True)
    resting_calories = Column(Integer, nullable=True)
    resting_heart_rate = Column(Integer, nullable=True)
    avg_stress = Column(Integer, nullable=True)
    body_battery_high = Column(Integer, nullable=True)
    body_battery_low = Column(Integer, nullable=True)
    synced_at = Column(DateTime, default=lambda: datetime.now(UTC))
    garmin_sync_success = Column(Boolean, default=True)

    def __repr__(self) -> str:
        return f"<DailyMetrics date={self.date} steps={self.steps} sleep={self.sleep_hours}h>"


class SyncLog(Base):
    """Records each sync attempt with its outcome."""

    __tablename__ = "sync_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sync_date = Column(DateTime, default=lambda: datetime.now(UTC))
    status = Column(String(20), nullable=False)  # "success" | "partial" | "error"
    error_message = Column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<SyncLog {self.sync_date} status={self.status}>"


class UserGoal(Base):
    """User-defined health targets."""
    __tablename__ = "user_goals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    metric = Column(String(30), unique=True, nullable=False)  # "steps" | "sleep_hours"
    target_value = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC))

    def __repr__(self) -> str:
        return f"<UserGoal {self.metric}={self.target_value}>"


class FoodEntry(Base):
    __tablename__ = "food_entries"
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    quantity = Column(Float, nullable=False, default=1.0)
    unit = Column(String(20), nullable=False, default="un")
    calories = Column(Float, nullable=True)
    protein_g = Column(Float, nullable=True)
    fat_g = Column(Float, nullable=True)
    carbs_g = Column(Float, nullable=True)
    fiber_g = Column(Float, nullable=True)
    source = Column(String(30), nullable=False, default="openfoodfacts")
    barcode = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
