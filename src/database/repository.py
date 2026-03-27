"""Database repository: all read/write operations for Garmin data."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any, Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, DailyMetrics, FoodCache, FoodEntry, GarminActivity, MealPreset, MealPresetItem, NewsletterInsight, NewsletterPost, SyncLog, TrainingEntry, UserGoal, UserSetting, WaistEntry, WaterEntry

logger = logging.getLogger(__name__)


class Repository:
    """Handles all database operations using SQLAlchemy."""

    def __init__(self, database_path: str) -> None:
        url = f"sqlite:///{database_path}"
        self._engine = create_engine(url, connect_args={"check_same_thread": False})
        # expire_on_commit=False lets ORM objects be used after session.close()
        self._Session = sessionmaker(bind=self._engine, expire_on_commit=False)

    def init_database(self) -> None:
        """Create all tables if they don't already exist."""
        Base.metadata.create_all(self._engine)
        logger.info("Database initialised at %s", self._engine.url)
        # Safe migrations for existing databases
        self._run_migrations()

    def _run_migrations(self) -> None:
        """Apply any schema changes that are not yet present (idempotent)."""
        from sqlalchemy import text, inspect
        with self._engine.connect() as conn:
            inspector = inspect(self._engine)
            existing_cols = {c["name"] for c in inspector.get_columns("daily_metrics")}
            new_cols = {
                "resting_heart_rate": "INTEGER",
                "avg_stress": "INTEGER",
                "body_battery_high": "INTEGER",
                "body_battery_low": "INTEGER",
                "weight_kg": "REAL",
                "total_calories": "INTEGER",
                "sleep_deep_min": "INTEGER",
                "sleep_light_min": "INTEGER",
                "sleep_rem_min": "INTEGER",
                "sleep_awake_min": "INTEGER",
                "floors_ascended": "INTEGER",
                "intensity_moderate_min": "INTEGER",
                "intensity_vigorous_min": "INTEGER",
                "spo2_avg": "REAL",
            }
            for col, col_type in new_cols.items():
                if col not in existing_cols:
                    conn.execute(text(f"ALTER TABLE daily_metrics ADD COLUMN {col} {col_type}"))
                    logger.info("Migration: added column daily_metrics.%s", col)
            conn.commit()
            # Create food_entries table if missing
            if "food_entries" not in inspector.get_table_names():
                FoodEntry.__table__.create(self._engine)
                logger.info("Migration: created table food_entries")
            # Create meal preset tables if missing
            table_names = inspector.get_table_names()
            if "meal_presets" not in table_names:
                MealPreset.__table__.create(self._engine)
                logger.info("Migration: created table meal_presets")
            if "meal_preset_items" not in table_names:
                MealPresetItem.__table__.create(self._engine)
                logger.info("Migration: created table meal_preset_items")
            if "user_settings" not in table_names:
                UserSetting.__table__.create(self._engine)
                logger.info("Migration: created table user_settings")
            if "training_entries" not in table_names:
                TrainingEntry.__table__.create(self._engine)
                logger.info("Migration: created table training_entries")
            if "waist_entries" not in table_names:
                WaistEntry.__table__.create(self._engine)
                logger.info("Migration: created table waist_entries")
            if "garmin_activities" not in table_names:
                GarminActivity.__table__.create(self._engine)
                logger.info("Migration: created table garmin_activities")
            if "food_cache" not in table_names:
                FoodCache.__table__.create(self._engine)
                logger.info("Migration: created table food_cache")
            if "newsletter_posts" not in table_names:
                NewsletterPost.__table__.create(self._engine)
                logger.info("Migration: created table newsletter_posts")
            if "newsletter_insights" not in table_names:
                NewsletterInsight.__table__.create(self._engine)
                logger.info("Migration: created table newsletter_insights")

    @contextmanager
    def _session(self) -> Generator[Session, None, None]:
        session = self._Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    # Write operations                                                      #
    # ------------------------------------------------------------------ #

    def save_daily_metrics(self, day: date, metrics: dict[str, Any]) -> None:
        """Insert or update daily metrics for the given date.

        Args:
            day: Calendar date the metrics belong to.
            metrics: Dict with keys matching DailyMetrics columns.
        """
        with self._session() as session:
            existing = session.query(DailyMetrics).filter_by(date=day).first()
            if existing:
                for key, value in metrics.items():
                    if hasattr(existing, key):
                        setattr(existing, key, value)
                existing.synced_at = datetime.now(UTC)
            else:
                row = DailyMetrics(date=day, synced_at=datetime.now(UTC), **{
                    k: v for k, v in metrics.items() if hasattr(DailyMetrics, k)
                })
                session.add(row)
        logger.debug("Saved metrics for %s", day)

    def log_sync(self, status: str, error_message: str | None = None) -> None:
        """Record a sync attempt.

        Args:
            status: "success", "partial", or "error".
            error_message: Optional description of the failure.
        """
        with self._session() as session:
            session.add(SyncLog(
                sync_date=datetime.now(UTC),
                status=status,
                error_message=error_message,
            ))

    # ------------------------------------------------------------------ #
    # Read operations                                                       #
    # ------------------------------------------------------------------ #

    def get_metrics_by_date(self, day: date) -> DailyMetrics | None:
        """Fetch metrics for a single day."""
        with self._session() as session:
            return session.query(DailyMetrics).filter_by(date=day).first()

    def get_metrics_range(self, start_date: date, end_date: date) -> list[DailyMetrics]:
        """Fetch all metrics rows between start_date and end_date (inclusive)."""
        with self._session() as session:
            return (
                session.query(DailyMetrics)
                .filter(DailyMetrics.date >= start_date, DailyMetrics.date <= end_date)
                .order_by(DailyMetrics.date)
                .all()
            )

    def get_weekly_stats(self, end_date: date) -> dict[str, Any]:
        """Calculate 7-day averages ending on end_date (inclusive).

        Returns a dict with avg/min/max/total values for sleep and activity.
        """
        start = end_date - timedelta(days=6)
        rows = self.get_metrics_range(start, end_date)

        if not rows:
            return {}

        sleep_hours = [r.sleep_hours for r in rows if r.sleep_hours is not None]
        sleep_scores = [r.sleep_score for r in rows if r.sleep_score is not None]
        steps = [r.steps for r in rows if r.steps is not None]
        active_cals = [r.active_calories for r in rows if r.active_calories is not None]
        resting_cals = [r.resting_calories for r in rows if r.resting_calories is not None]

        def _avg(lst: list) -> float | None:
            return round(sum(lst) / len(lst), 2) if lst else None

        # Find best/worst sleep day
        best_sleep_row = max((r for r in rows if r.sleep_hours), key=lambda r: r.sleep_hours, default=None)
        worst_sleep_row = min((r for r in rows if r.sleep_hours), key=lambda r: r.sleep_hours, default=None)

        return {
            "start_date": start,
            "end_date": end_date,
            "days_with_data": len(rows),
            "sleep_avg_hours": _avg(sleep_hours),
            "sleep_avg_score": round(sum(sleep_scores) / len(sleep_scores)) if sleep_scores else None,
            "sleep_best_hours": best_sleep_row.sleep_hours if best_sleep_row else None,
            "sleep_best_day": best_sleep_row.date if best_sleep_row else None,
            "sleep_worst_hours": worst_sleep_row.sleep_hours if worst_sleep_row else None,
            "sleep_worst_day": worst_sleep_row.date if worst_sleep_row else None,
            "steps_avg": int(_avg(steps)) if steps else None,
            "steps_total": sum(steps) if steps else None,
            "active_calories_total": sum(active_cals) if active_cals else None,
            "resting_calories_total": sum(resting_cals) if resting_cals else None,
        }

    def get_monthly_stats(self, end_date: date) -> dict[str, Any]:
        """Calculate 30-day averages ending on end_date (inclusive)."""
        start = end_date - timedelta(days=29)
        rows = self.get_metrics_range(start, end_date)

        if not rows:
            return {}

        sleep_hours = [r.sleep_hours for r in rows if r.sleep_hours is not None]
        steps = [r.steps for r in rows if r.steps is not None]
        active_cals = [r.active_calories for r in rows if r.active_calories is not None]

        def _avg(lst: list) -> float | None:
            return round(sum(lst) / len(lst), 2) if lst else None

        return {
            "start_date": start,
            "end_date": end_date,
            "days_with_data": len(rows),
            "sleep_avg_hours": _avg(sleep_hours),
            "steps_avg": int(_avg(steps)) if steps else None,
            "steps_total": sum(steps) if steps else None,
            "active_calories_total": sum(active_cals) if active_cals else None,
        }

    def get_recent_sync_logs(self, limit: int = 5) -> list[SyncLog]:
        """Return the most recent sync log entries."""
        with self._session() as session:
            return (
                session.query(SyncLog)
                .order_by(SyncLog.sync_date.desc())
                .limit(limit)
                .all()
            )

    def get_last_successful_sync(self) -> SyncLog | None:
        """Return the most recent successful sync log entry."""
        with self._session() as session:
            return (
                session.query(SyncLog)
                .filter_by(status="success")
                .order_by(SyncLog.sync_date.desc())
                .first()
            )

    def count_stored_days(self) -> int:
        """Return total number of days stored in daily_metrics."""
        with self._session() as session:
            return session.query(DailyMetrics).count()

    def has_successful_sync_today(self) -> bool:
        """Return True if there is a successful sync log entry for today (UTC)."""
        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        with self._session() as session:
            result = (
                session.query(SyncLog)
                .filter(SyncLog.status == "success", SyncLog.sync_date >= today_start)
                .first()
            )
            return result is not None

    def has_report_sent_today(self) -> bool:
        """Return True if a daily report was already sent today (UTC)."""
        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        with self._session() as session:
            result = (
                session.query(SyncLog)
                .filter(SyncLog.status == "report_sent", SyncLog.sync_date >= today_start)
                .first()
            )
            return result is not None

    def log_report_sent(self) -> None:
        """Record that the daily report was sent today."""
        self.log_sync("report_sent")

    def get_missing_dates(self, start_date: date, end_date: date) -> list[date]:
        """Return dates in [start_date, end_date] that have no entry in daily_metrics."""
        rows = self.get_metrics_range(start_date, end_date)
        existing = {r.date for r in rows}
        current = start_date
        missing = []
        while current <= end_date:
            if current not in existing:
                missing.append(current)
            current += timedelta(days=1)
        return missing

    def get_all_metrics(self, limit_days: int | None = None) -> list:
        """Return all daily_metrics rows ordered by date, optionally limited to last N days."""
        with self._session() as session:
            q = session.query(DailyMetrics).order_by(DailyMetrics.date.desc())
            if limit_days:
                q = q.limit(limit_days)
            rows = q.all()
            return list(reversed(rows))

    def get_goals(self) -> dict[str, float]:
        """Return all user goals as {metric: target_value}. Uses defaults if not set."""
        from .models import UserGoal
        defaults = {"steps": 10000.0, "sleep_hours": 7.0}
        with self._session() as session:
            rows = session.query(UserGoal).all()
            result = dict(defaults)
            for row in rows:
                result[row.metric] = row.target_value
            return result

    def set_goal(self, metric: str, target_value: float) -> None:
        """Insert or update a user goal."""
        from .models import UserGoal
        with self._session() as session:
            existing = session.query(UserGoal).filter_by(metric=metric).first()
            if existing:
                existing.target_value = target_value
                existing.updated_at = datetime.now(UTC)
            else:
                session.add(UserGoal(metric=metric, target_value=target_value))

    def get_previous_weekly_stats(self, end_date: date) -> dict:
        """Calculate stats for the 7 days ending 7 days before end_date (the prior week)."""
        prev_end = end_date - timedelta(days=7)
        return self.get_weekly_stats(prev_end)

    # ------------------------------------------------------------------ #
    # Weight operations                                                     #
    # ------------------------------------------------------------------ #

    def save_manual_weight(self, day: date, weight_kg: float) -> None:
        """Save or update weight for a given day.

        If a DailyMetrics row exists for the day, update its weight_kg.
        Otherwise create a new row with just the weight.
        """
        with self._session() as session:
            existing = session.query(DailyMetrics).filter_by(date=day).first()
            if existing:
                existing.weight_kg = weight_kg
            else:
                session.add(DailyMetrics(date=day, weight_kg=weight_kg, garmin_sync_success=False))
        logger.debug("Saved manual weight %.1f kg for %s", weight_kg, day)

    def get_latest_weight(self, before_date: date | None = None) -> tuple[float | None, date | None]:
        """Return the most recent (weight_kg, date) pair, or (None, None)."""
        with self._session() as session:
            q = session.query(DailyMetrics).filter(DailyMetrics.weight_kg.isnot(None))
            if before_date:
                q = q.filter(DailyMetrics.date <= before_date)
            row = q.order_by(DailyMetrics.date.desc()).first()
            if row:
                return row.weight_kg, row.date
            return None, None

    def get_weekly_weight_stats(self, end_date: date) -> dict[str, Any]:
        """Calculate weight stats for the 7 days ending on end_date.

        Returns dict with: current_weight, current_date, prev_weight, prev_date,
        delta, min_weight, max_weight, entries_count.
        Empty dict if no weight data.
        """
        start = end_date - timedelta(days=6)
        rows = self.get_metrics_range(start, end_date)
        weight_rows = [(r.weight_kg, r.date) for r in rows if r.weight_kg is not None]

        if not weight_rows:
            return {}

        weights = [w for w, _ in weight_rows]
        current_weight, current_date = weight_rows[-1]

        # Previous week's last weight for delta
        prev_start = start - timedelta(days=7)
        prev_rows = self.get_metrics_range(prev_start, start - timedelta(days=1))
        prev_weight_rows = [(r.weight_kg, r.date) for r in prev_rows if r.weight_kg is not None]
        prev_weight = prev_weight_rows[-1][0] if prev_weight_rows else None
        prev_date = prev_weight_rows[-1][1] if prev_weight_rows else None

        delta = round(current_weight - prev_weight, 1) if prev_weight is not None else None

        return {
            "current_weight": current_weight,
            "current_date": current_date,
            "prev_weight": prev_weight,
            "prev_date": prev_date,
            "delta": delta,
            "min_weight": min(weights),
            "max_weight": max(weights),
            "entries_count": len(weight_rows),
        }

    def get_recent_weight_records(self, limit: int = 10) -> list[tuple[date, float]]:
        """Return the last `limit` weight records as (date, kg) pairs, newest first."""
        with self._session() as session:
            rows = (
                session.query(DailyMetrics.date, DailyMetrics.weight_kg)
                .filter(DailyMetrics.weight_kg.isnot(None))
                .order_by(DailyMetrics.date.desc())
                .limit(limit)
                .all()
            )
            return [(r.date, r.weight_kg) for r in rows]

    def get_weight_records_range(self, days: int = 90) -> list[tuple[date, float]]:
        """Return weight records for the last `days` days as (date, kg) pairs, oldest first."""
        from datetime import date as _date, timedelta
        cutoff = _date.today() - timedelta(days=days)
        with self._session() as session:
            rows = (
                session.query(DailyMetrics.date, DailyMetrics.weight_kg)
                .filter(DailyMetrics.weight_kg.isnot(None), DailyMetrics.date >= cutoff)
                .order_by(DailyMetrics.date.asc())
                .all()
            )
            return [(r.date, r.weight_kg) for r in rows]

    # ------------------------------------------------------------------ #
    # Waist operations                                                      #
    # ------------------------------------------------------------------ #

    def save_waist_entry(self, day: date, waist_cm: float) -> None:
        """Save or update waist circumference measurement for a given day."""
        with self._session() as session:
            existing = session.query(WaistEntry).filter_by(date=day).first()
            if existing:
                existing.waist_cm = waist_cm
            else:
                session.add(WaistEntry(date=day, waist_cm=waist_cm))
        logger.debug("Saved waist measurement %.1f cm for %s", waist_cm, day)

    def get_recent_waist_records(self, limit: int = 10) -> list[tuple[date, float]]:
        """Return the last `limit` waist records as (date, cm) pairs, newest first."""
        with self._session() as session:
            rows = (
                session.query(WaistEntry.date, WaistEntry.waist_cm)
                .order_by(WaistEntry.date.desc())
                .limit(limit)
                .all()
            )
            return [(r.date, r.waist_cm) for r in rows]

    # ------------------------------------------------------------------ #
    # Water operations                                                      #
    # ------------------------------------------------------------------ #

    def add_water_entry(self, day: date, ml: int) -> None:
        """Add a water intake entry for the given day."""
        with self._session() as session:
            session.add(WaterEntry(date=day, ml=ml))
        logger.debug("Added water entry %d ml for %s", ml, day)

    def get_daily_water(self, day: date) -> int:
        """Return total ml of water logged for the given day (0 if none)."""
        from sqlalchemy import func
        with self._session() as session:
            total = session.query(func.sum(WaterEntry.ml)).filter(WaterEntry.date == day).scalar()
            return int(total) if total else 0

    def get_weekly_water_avg(self, end_date: date) -> float | None:
        """Return average daily ml of water over the last 7 days ending on end_date.

        Returns None if no water data in that period.
        """
        from sqlalchemy import func
        start_date = end_date - timedelta(days=6)
        with self._session() as session:
            rows = (
                session.query(WaterEntry.date, func.sum(WaterEntry.ml).label("total_ml"))
                .filter(WaterEntry.date >= start_date, WaterEntry.date <= end_date)
                .group_by(WaterEntry.date)
                .all()
            )
        if not rows:
            return None
        return sum(r.total_ml for r in rows) / 7  # avg over 7 days, not just days with data

    # ------------------------------------------------------------------ #
    # Nutrition operations                                                  #
    # ------------------------------------------------------------------ #

    def save_food_entries(self, day: date, entries: list[dict]) -> list[int]:
        """Save multiple food entries for a day. Returns list of IDs."""
        ids = []
        with self._session() as session:
            for entry in entries:
                row = FoodEntry(date=day, **{k: v for k, v in entry.items() if hasattr(FoodEntry, k)})
                session.add(row)
                session.flush()
                ids.append(row.id)
        return ids

    def get_food_entries(self, day: date) -> list[FoodEntry]:
        """Return all food entries for a day, ordered by created_at."""
        with self._session() as session:
            return (
                session.query(FoodEntry)
                .filter_by(date=day)
                .order_by(FoodEntry.created_at)
                .all()
            )

    def get_food_entries_range(self, start_date: date, end_date: date) -> list[FoodEntry]:
        """Return all food entries between start_date and end_date (inclusive), ordered by date and created_at."""
        with self._session() as session:
            return (
                session.query(FoodEntry)
                .filter(FoodEntry.date >= start_date, FoodEntry.date <= end_date)
                .order_by(FoodEntry.date, FoodEntry.created_at)
                .all()
            )

    def delete_last_food_entry(self, day: date) -> FoodEntry | None:
        """Delete the most recent food entry for the day. Returns deleted entry or None."""
        with self._session() as session:
            row = (
                session.query(FoodEntry)
                .filter_by(date=day)
                .order_by(FoodEntry.created_at.desc())
                .first()
            )
            if row:
                session.delete(row)
                return row
            return None

    def get_daily_nutrition(self, day: date) -> dict:
        """Return summed nutrition totals for the day. Returns zeros if no data."""
        from sqlalchemy import func
        with self._session() as session:
            result = session.query(
                func.sum(FoodEntry.calories).label("calories"),
                func.sum(FoodEntry.protein_g).label("protein_g"),
                func.sum(FoodEntry.fat_g).label("fat_g"),
                func.sum(FoodEntry.carbs_g).label("carbs_g"),
                func.sum(FoodEntry.fiber_g).label("fiber_g"),
                func.count(FoodEntry.id).label("entry_count"),
            ).filter(FoodEntry.date == day).one()
            return {
                "calories": result.calories or 0.0,
                "protein_g": result.protein_g or 0.0,
                "fat_g": result.fat_g or 0.0,
                "carbs_g": result.carbs_g or 0.0,
                "fiber_g": result.fiber_g or 0.0,
                "entry_count": result.entry_count or 0,
            }

    def get_weekly_nutrition(self, end_date: date) -> dict:
        """Return daily averages for nutrition over last 7 days ending on end_date."""
        from datetime import timedelta
        from sqlalchemy import func
        start = end_date - timedelta(days=6)
        with self._session() as session:
            result = session.query(
                func.sum(FoodEntry.calories).label("calories"),
                func.sum(FoodEntry.protein_g).label("protein_g"),
                func.sum(FoodEntry.fat_g).label("fat_g"),
                func.sum(FoodEntry.carbs_g).label("carbs_g"),
                func.sum(FoodEntry.fiber_g).label("fiber_g"),
                func.count(func.distinct(FoodEntry.date)).label("days_with_data"),
            ).filter(FoodEntry.date >= start, FoodEntry.date <= end_date).one()
            days = result.days_with_data or 1
            def _avg(v):
                return round((v or 0.0) / days, 1)
            return {
                "avg_calories": _avg(result.calories),
                "avg_protein": _avg(result.protein_g),
                "avg_fat": _avg(result.fat_g),
                "avg_carbs": _avg(result.carbs_g),
                "avg_fiber": _avg(result.fiber_g),
                "days_with_data": result.days_with_data or 0,
            }

    # ------------------------------------------------------------------ #
    # Food cache operations                                               #
    # ------------------------------------------------------------------ #

    def get_food_cache(self, query_text: str) -> list[dict] | None:
        """Return cached FoodItemResult dicts for the normalised query, or None on miss.

        Also bumps use_count and last_used_at on a hit.
        """
        import json
        normalized = query_text.lower().strip()
        with self._session() as session:
            entry = session.get(FoodCache, normalized)
            if entry is None:
                return None
            entry.use_count += 1
            entry.last_used_at = datetime.now(UTC)
            return json.loads(entry.items_json)

    def set_food_cache(self, query_text: str, items: list[dict]) -> None:
        """Store or overwrite the LLM result for the given query."""
        import json
        normalized = query_text.lower().strip()
        items_json = json.dumps(items, ensure_ascii=False)
        with self._session() as session:
            entry = session.get(FoodCache, normalized)
            if entry is None:
                session.add(FoodCache(query_text=normalized, items_json=items_json))
            else:
                entry.items_json = items_json
                entry.last_used_at = datetime.now(UTC)
        logger.debug("Food cache set for query %r", normalized)

    # ------------------------------------------------------------------ #
    # Meal preset operations                                               #
    # ------------------------------------------------------------------ #

    def save_meal_preset(self, name: str, items: list[dict]) -> MealPreset:
        """Create or replace a meal preset with the given items.

        If a preset with this name already exists, it is deleted and recreated
        (cascade delete removes old items). Name matching is case-insensitive.

        Args:
            name: Preset name (e.g. "Lanche").
            items: List of dicts with keys: name, quantity, unit, calories,
                   protein_g, fat_g, carbs_g, fiber_g.

        Returns:
            The newly created MealPreset ORM object (detached).
        """
        with self._session() as session:
            # Delete existing preset with same name (case-insensitive)
            existing = (
                session.query(MealPreset)
                .filter(MealPreset.name.ilike(name))
                .first()
            )
            if existing:
                session.delete(existing)
                session.flush()

            preset = MealPreset(name=name)
            session.add(preset)
            session.flush()  # get preset.id

            for item in items:
                session.add(MealPresetItem(
                    preset_id=preset.id,
                    name=item["name"],
                    quantity=item.get("quantity", 1.0),
                    unit=item.get("unit", "un"),
                    calories=item.get("calories"),
                    protein_g=item.get("protein_g"),
                    fat_g=item.get("fat_g"),
                    carbs_g=item.get("carbs_g"),
                    fiber_g=item.get("fiber_g"),
                ))
            logger.debug("Saved meal preset %r with %d items", name, len(items))
            return preset

    def get_meal_preset_by_name(self, name: str) -> MealPreset | None:
        """Fetch a preset by name (case-insensitive). Returns None if not found."""
        with self._session() as session:
            preset = (
                session.query(MealPreset)
                .filter(MealPreset.name.ilike(name))
                .first()
            )
            if preset is None:
                return None
            # Eagerly load items while session is open
            _ = [item.name for item in preset.items]
            return preset

    def list_meal_presets(self) -> list[MealPreset]:
        """Return all meal presets ordered by name, with items loaded."""
        with self._session() as session:
            presets = (
                session.query(MealPreset)
                .order_by(MealPreset.name)
                .all()
            )
            # Eagerly load items while session is open
            for preset in presets:
                _ = [item.name for item in preset.items]
            return presets

    def delete_meal_preset(self, name: str) -> bool:
        """Delete a preset by name (case-insensitive). Returns True if deleted."""
        with self._session() as session:
            preset = (
                session.query(MealPreset)
                .filter(MealPreset.name.ilike(name))
                .first()
            )
            if preset is None:
                return False
            session.delete(preset)
            logger.debug("Deleted meal preset %r", name)
            return True

    # ------------------------------------------------------------------ #
    # User settings operations                                             #
    # ------------------------------------------------------------------ #

    def get_setting(self, key: str) -> str | None:
        """Return the value for a setting key, or None if not set."""
        with self._session() as session:
            row = session.query(UserSetting).filter_by(key=key).first()
            return row.value if row else None

    def set_setting(self, key: str, value: str) -> None:
        """Insert or update a setting key-value pair."""
        with self._session() as session:
            existing = session.query(UserSetting).filter_by(key=key).first()
            if existing:
                existing.value = value
                existing.updated_at = datetime.now(UTC)
            else:
                session.add(UserSetting(key=key, value=value))
        logger.debug("Setting %r updated to %r", key, value)

    # ------------------------------------------------------------------ #
    # Training log operations                                              #
    # ------------------------------------------------------------------ #

    def upsert_training_entry(self, day: date, description: str) -> None:
        """Insert or update the training entry for a given day."""
        with self._session() as session:
            existing = session.query(TrainingEntry).filter_by(date=day).first()
            if existing:
                existing.description = description
                existing.created_at = datetime.now(UTC)
            else:
                session.add(TrainingEntry(date=day, description=description))
        logger.debug("Training entry saved for %s", day)

    def get_recent_training(self, days: int = 7) -> list[TrainingEntry]:
        """Return training entries for the last N days, ordered by date descending."""
        cutoff = date.today() - timedelta(days=days)
        with self._session() as session:
            return (
                session.query(TrainingEntry)
                .filter(TrainingEntry.date > cutoff)
                .order_by(TrainingEntry.date.desc())
                .all()
            )

    def search_training_entries(self, query: str, limit: int = 30) -> list[dict]:
        """Return training entries whose description contains `query` (case-insensitive).

        Returns list of {"date": date, "description": str} dicts ordered oldest-first.
        """
        with self._session() as session:
            rows = (
                session.query(TrainingEntry)
                .filter(TrainingEntry.description.ilike(f"%{query}%"))
                .order_by(TrainingEntry.date.asc())
                .limit(limit)
                .all()
            )
            return [{"date": r.date, "description": r.description} for r in rows]

    # ------------------------------------------------------------------ #
    # Garmin activities (auto-sync)                                       #
    # ------------------------------------------------------------------ #

    def upsert_garmin_activity(
        self,
        activity_id: int,
        day: date,
        name: str,
        type_key: str | None,
        duration_min: int | None,
        calories: int | None,
        distance_km: float | None,
    ) -> None:
        """Insert or update a Garmin activity (keyed by activity_id — no duplicates)."""
        with self._session() as session:
            existing = session.query(GarminActivity).filter_by(
                garmin_activity_id=activity_id
            ).first()
            if existing:
                existing.name = name
                existing.type_key = type_key
                existing.duration_min = duration_min
                existing.calories = calories
                existing.distance_km = distance_km
                existing.synced_at = datetime.now(UTC)
            else:
                session.add(GarminActivity(
                    garmin_activity_id=activity_id,
                    date=day,
                    name=name,
                    type_key=type_key,
                    duration_min=duration_min,
                    calories=calories,
                    distance_km=distance_km,
                ))
        logger.debug("Garmin activity %d upserted for %s", activity_id, day)

    def get_garmin_activities_for_date(self, day: date) -> list[GarminActivity]:
        """Return all Garmin activities for a specific date."""
        with self._session() as session:
            return (
                session.query(GarminActivity)
                .filter(GarminActivity.date == day)
                .order_by(GarminActivity.garmin_activity_id)
                .all()
            )

    def get_training_summary_for_llm(self, days: int = 7) -> list[dict]:
        """Return combined manual + Garmin training entries for the last N days.

        Returns a list of {date, description} dicts, one per day that has any
        training data, ordered by date descending.
        """
        cutoff = date.today() - timedelta(days=days)
        result: dict[date, list[str]] = {}

        with self._session() as session:
            # Manual entries
            manual = (
                session.query(TrainingEntry)
                .filter(TrainingEntry.date > cutoff)
                .all()
            )
            for entry in manual:
                result.setdefault(entry.date, []).append(entry.description)

            # Garmin activities
            garmin = (
                session.query(GarminActivity)
                .filter(GarminActivity.date > cutoff)
                .order_by(GarminActivity.garmin_activity_id)
                .all()
            )
            for act in garmin:
                parts = [act.name]
                if act.duration_min is not None:
                    parts.append(f"{act.duration_min}min")
                if act.calories is not None:
                    parts.append(f"{act.calories}kcal")
                if act.distance_km is not None:
                    parts.append(f"{act.distance_km}km")
                result.setdefault(act.date, []).append(" ".join(parts))

        return [
            {"date": str(d), "description": " | ".join(descs)}
            for d, descs in sorted(result.items(), reverse=True)
        ]

    def get_weekly_training_load(self, end_date: date) -> dict[str, dict]:
        """Return Garmin activity totals for the 7-day period ending on end_date.

        Returns:
            Dict mapping type_key -> {"minutes": int, "km": float, "count": int}.
            type_key "other" is used when type_key is None.
        """
        start_date = end_date - timedelta(days=6)
        with self._session() as session:
            rows = (
                session.query(GarminActivity)
                .filter(GarminActivity.date >= start_date, GarminActivity.date <= end_date)
                .all()
            )
        totals: dict[str, dict] = {}
        for row in rows:
            key = row.type_key or "other"
            entry = totals.setdefault(key, {"minutes": 0, "km": 0.0, "count": 0})
            entry["count"] += 1
            entry["minutes"] += row.duration_min or 0
            entry["km"] += row.distance_km or 0.0
        return totals

    # ------------------------------------------------------------------ #
    # Newsletter                                                            #
    # ------------------------------------------------------------------ #

    def get_latest_newsletter_post_date(self) -> date | None:
        """Return the published_date of the most recently scraped post, or None."""
        with self._session() as session:
            row = (
                session.query(NewsletterPost)
                .order_by(NewsletterPost.published_date.desc())
                .first()
            )
            return row.published_date if row else None

    def save_newsletter_post(self, url: str, title: str, published_date: date | None, content_text: str) -> NewsletterPost:
        """Insert a newsletter post; silently skip if URL already exists."""
        url = url[:490]
        with self._session() as session:
            existing = session.query(NewsletterPost).filter_by(url=url).first()
            if existing:
                return existing
            post = NewsletterPost(
                url=url,
                title=title,
                published_date=published_date,
                content_text=content_text,
            )
            session.add(post)
        return post

    def get_all_newsletter_posts(self) -> list[NewsletterPost]:
        """Return all stored posts ordered by date ascending."""
        with self._session() as session:
            rows = (
                session.query(NewsletterPost)
                .order_by(NewsletterPost.published_date.asc())
                .all()
            )
            for row in rows:
                session.expunge(row)
            return rows

    def save_newsletter_insight(
        self,
        insight_pt: str,
        insight_type: str,
        post_url: str | None = None,
        metrics_context: str | None = None,
    ) -> NewsletterInsight:
        """Persist a generated newsletter insight."""
        if insight_type not in ("daily", "historical"):
            raise ValueError(f"Invalid insight_type: {insight_type!r}. Must be 'daily' or 'historical'.")
        with self._session() as session:
            insight = NewsletterInsight(
                post_url=post_url,
                insight_type=insight_type,
                insight_pt=insight_pt,
                metrics_context=metrics_context,
                sent=False,
            )
            session.add(insight)
        return insight

    def get_unsent_daily_insight(self) -> NewsletterInsight | None:
        """Return the most recent unsent daily insight, or None."""
        with self._session() as session:
            row = (
                session.query(NewsletterInsight)
                .filter_by(insight_type="daily", sent=False)
                .order_by(NewsletterInsight.generated_at.desc())
                .first()
            )
            if row:
                session.expunge(row)
            return row

    def get_latest_daily_insight(self) -> NewsletterInsight | None:
        """Return the most recently generated daily insight (sent or not), or None."""
        with self._session() as session:
            row = (
                session.query(NewsletterInsight)
                .filter_by(insight_type="daily")
                .order_by(NewsletterInsight.generated_at.desc())
                .first()
            )
            if row:
                session.expunge(row)
            return row

    def mark_insight_sent(self, insight_id: int) -> None:
        """Mark a newsletter insight as sent."""
        with self._session() as session:
            insight = session.query(NewsletterInsight).filter_by(id=insight_id).first()
            if insight:
                insight.sent = True

    def get_latest_historical_insight(self) -> NewsletterInsight | None:
        """Return the most recently generated historical insight, or None."""
        with self._session() as session:
            row = (
                session.query(NewsletterInsight)
                .filter_by(insight_type="historical")
                .order_by(NewsletterInsight.generated_at.desc())
                .first()
            )
            if row:
                session.expunge(row)
            return row
