"""Garmin Connect data client: fetches sleep and activity metrics."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import garminconnect
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .auth import create_garmin_client, invalidate_token

logger = logging.getLogger(__name__)


@dataclass
class SleepData:
    hours: float | None
    score: int | None
    quality: str | None


@dataclass
class ActivityData:
    steps: int | None
    active_calories: int | None
    resting_calories: int | None


@dataclass
class DailySummary:
    date: date
    sleep: SleepData
    activity: ActivityData
    resting_heart_rate: int | None = None
    avg_stress: int | None = None
    body_battery_high: int | None = None
    body_battery_low: int | None = None


def _assess_sleep_quality(score: int | None) -> str | None:
    """Map a numeric sleep score to a Portuguese quality label."""
    if score is None:
        return None
    if score >= 80:
        return "Excelente"
    if score >= 70:
        return "Bom"
    if score >= 60:
        return "Razoável"
    return "Mau"


def _on_retry(retry_state) -> None:
    logger.warning(
        "Garmin API attempt %d failed: %s",
        retry_state.attempt_number,
        retry_state.outcome.exception(),
    )


class GarminClient:
    """High-level wrapper around garminconnect for fetching daily metrics."""

    def __init__(self, email: str, password: str) -> None:
        self._email = email
        self._password = password
        self._client: garminconnect.Garmin | None = None

    def authenticate(self) -> None:
        """Initialise (or refresh) the authenticated Garmin client."""
        self._client = create_garmin_client(self._email, self._password)

    def _ensure_authenticated(self) -> garminconnect.Garmin:
        if self._client is None:
            self.authenticate()
        return self._client  # type: ignore[return-value]

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        before_sleep=_on_retry,
        reraise=True,
    )
    def get_sleep_data(self, day: date) -> SleepData:
        """Fetch sleep metrics for the given date.

        Args:
            day: The calendar date to query.

        Returns:
            SleepData with hours, score, and quality (any may be None).
        """
        client = self._ensure_authenticated()
        date_str = day.isoformat()

        try:
            raw: dict[str, Any] = client.get_sleep_data(date_str)
        except garminconnect.GarminConnectAuthenticationError:
            logger.warning("Garmin: auth error, invalidating token and retrying")
            invalidate_token()
            self._client = None
            raise

        if not raw:
            logger.warning("Garmin: no sleep data for %s", date_str)
            return SleepData(hours=None, score=None, quality=None)

        daily = raw.get("dailySleepDTO", {})
        sleep_seconds = daily.get("sleepTimeSeconds")
        hours = round(sleep_seconds / 3600, 2) if sleep_seconds else None
        score = daily.get("sleepScores", {}).get("overall", {}).get("value")
        # Some firmware versions report score at top level
        if score is None:
            score = daily.get("averageSpO2Value")  # fallback not ideal; log it
        score_val = int(score) if score is not None else None

        return SleepData(
            hours=hours,
            score=score_val,
            quality=_assess_sleep_quality(score_val),
        )

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        before_sleep=_on_retry,
        reraise=True,
    )
    def get_activity_data(self, day: date) -> ActivityData:
        """Fetch activity metrics for the given date.

        Args:
            day: The calendar date to query.

        Returns:
            ActivityData with steps, active_calories, resting_calories.
        """
        client = self._ensure_authenticated()
        date_str = day.isoformat()

        try:
            raw: dict[str, Any] = client.get_stats(date_str)
        except garminconnect.GarminConnectAuthenticationError:
            logger.warning("Garmin: auth error, invalidating token and retrying")
            invalidate_token()
            self._client = None
            raise

        if not raw:
            logger.warning("Garmin: no activity data for %s", date_str)
            return ActivityData(steps=None, active_calories=None, resting_calories=None)

        steps = raw.get("totalSteps")
        active_cals = raw.get("activeKilocalories")
        resting_cals = raw.get("bmrKilocalories")

        return ActivityData(
            steps=int(steps) if steps is not None else None,
            active_calories=int(active_cals) if active_cals is not None else None,
            resting_calories=int(resting_cals) if resting_cals is not None else None,
        )

    def get_health_data(self, day: date) -> dict:
        """Fetch resting HR, avg stress, and body battery for the given date.
        Returns a dict with keys: resting_heart_rate, avg_stress, body_battery_high, body_battery_low.
        Any value may be None. Never raises — fails silently.
        """
        client = self._ensure_authenticated()
        date_str = day.isoformat()
        result = {"resting_heart_rate": None, "avg_stress": None, "body_battery_high": None, "body_battery_low": None}
        try:
            stats = client.get_stats(date_str)
            if stats:
                result["resting_heart_rate"] = stats.get("restingHeartRate")
        except Exception as exc:
            logger.debug("Could not fetch HR for %s: %s", date_str, exc)
        try:
            stress = client.get_stress_data(date_str)
            if stress:
                result["avg_stress"] = stress.get("avgStressLevel")
        except Exception as exc:
            logger.debug("Could not fetch stress for %s: %s", date_str, exc)
        try:
            bb = client.get_body_battery(date_str)
            if bb and isinstance(bb, list) and len(bb) > 0:
                values = [item.get("charged", 0) for item in bb if item.get("charged") is not None]
                if values:
                    result["body_battery_high"] = max(values)
                    result["body_battery_low"] = min(values)
        except Exception as exc:
            logger.debug("Could not fetch body battery for %s: %s", date_str, exc)
        return result

    def check_sleep_available(self, day: date) -> bool:
        """Check if completed sleep data exists for the given date.

        This is used for wake detection: Garmin only has sleep data for today
        once the user has woken up and their device has synced.

        Args:
            day: The date to check (typically today).

        Returns:
            True if sleep data with sleepTimeSeconds is available.
        """
        client = self._ensure_authenticated()
        date_str = day.isoformat()

        try:
            raw: dict[str, Any] = client.get_sleep_data(date_str)
        except garminconnect.GarminConnectAuthenticationError:
            logger.warning("Garmin: auth error during sleep check, invalidating token")
            invalidate_token()
            self._client = None
            return False
        except Exception as exc:
            logger.debug("Sleep availability check failed for %s: %s", date_str, exc)
            return False

        if not raw:
            return False

        daily = raw.get("dailySleepDTO", {})
        sleep_seconds = daily.get("sleepTimeSeconds")
        return sleep_seconds is not None and sleep_seconds > 0

    def get_yesterday_summary(self) -> DailySummary:
        """Convenience method: fetch all metrics for yesterday.

        The Garmin API assigns sleep to the date you *wake up*, not when you
        fell asleep. So "last night's sleep" (e.g. Thu→Fri) is returned when
        querying today's date (Fri). Activity (steps, calories) is correctly
        assigned to the day it occurred, so we query yesterday for that.

        Returns:
            DailySummary combining sleep and activity data, stored under yesterday's date.
        """
        today = date.today()
        yesterday = today - timedelta(days=1)
        logger.info("Fetching Garmin data: sleep for %s, activity for %s", today, yesterday)

        sleep = SleepData(hours=None, score=None, quality=None)
        activity = ActivityData(steps=None, active_calories=None, resting_calories=None)

        try:
            # Sleep: query today — Garmin tags last night's sleep with today's date
            sleep = self.get_sleep_data(today)
        except Exception as exc:
            logger.error("Failed to fetch sleep data: %s", exc)

        try:
            # Activity: query yesterday — steps/calories belong to the day they happened
            activity = self.get_activity_data(yesterday)
        except Exception as exc:
            logger.error("Failed to fetch activity data: %s", exc)

        health = self.get_health_data(yesterday)

        # Store everything under yesterday's date (the "day being reported")
        return DailySummary(date=yesterday, sleep=sleep, activity=activity, **health)

    def get_summary_for_date(self, day: date) -> DailySummary:
        """Fetch all metrics for a specific historical date.

        For historical dates, Garmin stores sleep under the wake-up date.
        Since we don't know the exact wake-up date for arbitrary past days,
        we try `day + 1` for sleep (the most common case) and fall back
        to `day` itself if no data is found.

        Args:
            day: The calendar date to report on (activity date).

        Returns:
            DailySummary with all available data.
        """
        logger.info("Fetching Garmin data for specific date: %s", day)
        sleep = SleepData(hours=None, score=None, quality=None)
        activity = ActivityData(steps=None, active_calories=None, resting_calories=None)

        # Try day+1 for sleep first (wake-up date convention), fall back to day
        for sleep_date in [day + timedelta(days=1), day]:
            try:
                candidate = self.get_sleep_data(sleep_date)
                if candidate.hours is not None:
                    sleep = candidate
                    break
            except Exception as exc:
                logger.debug("Sleep fetch for %s failed: %s", sleep_date, exc)

        try:
            activity = self.get_activity_data(day)
        except Exception as exc:
            logger.error("Failed to fetch activity data for %s: %s", day, exc)

        health = self.get_health_data(day)
        return DailySummary(date=day, sleep=sleep, activity=activity, **health)

    def to_metrics_dict(self, summary: DailySummary) -> dict[str, Any]:
        """Convert a DailySummary to a flat dict for the database repository.

        Args:
            summary: The daily summary to convert.

        Returns:
            Dict matching the DailyMetrics model columns.
        """
        has_data = any([
            summary.sleep.hours,
            summary.sleep.score,
            summary.activity.steps,
        ])
        return {
            "sleep_hours": summary.sleep.hours,
            "sleep_score": summary.sleep.score,
            "sleep_quality": summary.sleep.quality,
            "steps": summary.activity.steps,
            "active_calories": summary.activity.active_calories,
            "resting_calories": summary.activity.resting_calories,
            "resting_heart_rate": summary.resting_heart_rate,
            "avg_stress": summary.avg_stress,
            "body_battery_high": summary.body_battery_high,
            "body_battery_low": summary.body_battery_low,
            "garmin_sync_success": has_data,
        }
