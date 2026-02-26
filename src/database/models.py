"""SQLAlchemy ORM models for the Garmin data database."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, relationship


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
    total_calories = Column(Integer, nullable=True)
    resting_heart_rate = Column(Integer, nullable=True)
    avg_stress = Column(Integer, nullable=True)
    body_battery_high = Column(Integer, nullable=True)
    body_battery_low = Column(Integer, nullable=True)
    weight_kg = Column(Float, nullable=True)
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


class MealPreset(Base):
    """A named collection of food items that can be quickly registered."""
    __tablename__ = "meal_presets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    items = relationship("MealPresetItem", back_populates="preset",
                         cascade="all, delete-orphan", order_by="MealPresetItem.id")

    def __repr__(self) -> str:
        return f"<MealPreset name={self.name!r}>"


class MealPresetItem(Base):
    """One food item belonging to a MealPreset."""
    __tablename__ = "meal_preset_items"
    id = Column(Integer, primary_key=True, autoincrement=True)
    preset_id = Column(Integer, ForeignKey("meal_presets.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    quantity = Column(Float, nullable=False, default=1.0)
    unit = Column(String(20), nullable=False, default="un")
    calories = Column(Float, nullable=True)
    protein_g = Column(Float, nullable=True)
    fat_g = Column(Float, nullable=True)
    carbs_g = Column(Float, nullable=True)
    fiber_g = Column(Float, nullable=True)
    preset = relationship("MealPreset", back_populates="items")

    def __repr__(self) -> str:
        return f"<MealPresetItem {self.name!r} {self.quantity}{self.unit}>"


class UserSetting(Base):
    """Generic key-value store for user-configurable settings."""
    __tablename__ = "user_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC))

    def __repr__(self) -> str:
        return f"<UserSetting {self.key!r}={self.value!r}>"


class TrainingEntry(Base):
    """Records a training session done by the user on a given day."""
    __tablename__ = "training_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, unique=True, nullable=False, index=True)
    description = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    def __repr__(self) -> str:
        return f"<TrainingEntry date={self.date} description={self.description!r}>"


class GarminActivity(Base):
    """Auto-synced activity from Garmin Connect (deduplicated by garmin_activity_id)."""
    __tablename__ = "garmin_activities"

    garmin_activity_id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    type_key = Column(String(50), nullable=True)
    duration_min = Column(Integer, nullable=True)
    calories = Column(Integer, nullable=True)
    distance_km = Column(Float, nullable=True)
    synced_at = Column(DateTime, default=lambda: datetime.now(UTC))

    def __repr__(self) -> str:
        return f"<GarminActivity id={self.garmin_activity_id} date={self.date} name={self.name!r}>"


class WaistEntry(Base):
    """Records a waist circumference measurement for a given day."""
    __tablename__ = "waist_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, unique=True, nullable=False, index=True)
    waist_cm = Column(Float, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    def __repr__(self) -> str:
        return f"<WaistEntry date={self.date} waist_cm={self.waist_cm}>"


class FoodCache(Base):
    """Cache for /comi LLM results, keyed by normalised query text."""
    __tablename__ = "food_cache"

    query_text = Column(String(500), primary_key=True)  # lower-cased, stripped
    items_json = Column(Text, nullable=False)            # JSON list of FoodItemResult dicts
    use_count = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    last_used_at = Column(DateTime, default=lambda: datetime.now(UTC))

    def __repr__(self) -> str:
        return f"<FoodCache query={self.query_text!r} use_count={self.use_count}>"
