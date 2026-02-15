"""Database repository: all read/write operations for Garmin data."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any, Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, DailyMetrics, FoodEntry, SyncLog, UserGoal

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
