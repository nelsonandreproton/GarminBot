"""Read-only HTTP API for external integrations (e.g. OpenClaw).

Endpoints (all require Authorization: Bearer <GARMIN_API_KEY>):

  GET /api/metrics?days=7       — last N days of daily health metrics (max 30)
  GET /api/activities?days=14   — recent Garmin activities (max 30)
  GET /api/summary              — compact all-in-one payload for workout suggestions
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

_MAX_DAYS = 30


def _metrics_to_dict(row) -> dict:
    return {
        "date": str(row.date),
        "sleep_hours": row.sleep_hours,
        "sleep_score": row.sleep_score,
        "sleep_quality": row.sleep_quality,
        "sleep_deep_min": row.sleep_deep_min,
        "sleep_light_min": row.sleep_light_min,
        "sleep_rem_min": row.sleep_rem_min,
        "steps": row.steps,
        "active_calories": row.active_calories,
        "resting_calories": row.resting_calories,
        "total_calories": row.total_calories,
        "floors_ascended": row.floors_ascended,
        "resting_heart_rate": row.resting_heart_rate,
        "avg_stress": row.avg_stress,
        "body_battery_high": row.body_battery_high,
        "body_battery_low": row.body_battery_low,
        "spo2_avg": row.spo2_avg,
        "weight_kg": row.weight_kg,
        "intensity_moderate_min": row.intensity_moderate_min,
        "intensity_vigorous_min": row.intensity_vigorous_min,
    }


def _activity_to_dict(row) -> dict:
    return {
        "date": str(row.date),
        "name": row.name,
        "type": row.type_key,
        "duration_min": row.duration_min,
        "calories": row.calories,
        "distance_km": row.distance_km,
    }


def _make_handler(api_key: str, repo):
    class ApiHandler(BaseHTTPRequestHandler):
        def _check_auth(self) -> bool:
            auth = self.headers.get("Authorization", "")
            if auth == f"Bearer {api_key}":
                return True
            # Also accept X-API-Key header
            if self.headers.get("X-API-Key", "") == api_key:
                return True
            self._send_json({"error": "unauthorized"}, status=401)
            return False

        def _send_json(self, data: dict | list, status: int = 200) -> None:
            body = json.dumps(data, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _parse_days(self, params: dict, default: int) -> int:
            raw = params.get("days", [str(default)])[0]
            try:
                return min(int(raw), _MAX_DAYS)
            except ValueError:
                return default

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            if not self._check_auth():
                return

            if parsed.path == "/api/metrics":
                self._handle_metrics(params)
            elif parsed.path == "/api/activities":
                self._handle_activities(params)
            elif parsed.path == "/api/summary":
                self._handle_summary()
            else:
                self._send_json({"error": "not found"}, status=404)

        def _handle_metrics(self, params: dict) -> None:
            days = self._parse_days(params, default=7)
            end = date.today() - timedelta(days=1)
            start = end - timedelta(days=days - 1)
            rows = repo.get_metrics_range(start, end)
            self._send_json([_metrics_to_dict(r) for r in rows])

        def _handle_activities(self, params: dict) -> None:
            days = self._parse_days(params, default=14)
            end = date.today()
            start = end - timedelta(days=days - 1)
            # Fetch activities day by day and aggregate
            activities = []
            for i in range(days):
                day = end - timedelta(days=i)
                for act in repo.get_garmin_activities_for_date(day):
                    activities.append(_activity_to_dict(act))
            activities.sort(key=lambda a: a["date"], reverse=True)
            self._send_json(activities)

        def _handle_summary(self) -> None:
            """Compact payload designed for workout suggestion prompts."""
            # Last 7 days of health metrics
            end = date.today() - timedelta(days=1)
            start = end - timedelta(days=6)
            metrics = [_metrics_to_dict(r) for r in repo.get_metrics_range(start, end)]

            # Last 14 days of Garmin activities
            act_end = date.today()
            activities = []
            for i in range(14):
                day = act_end - timedelta(days=i)
                for act in repo.get_garmin_activities_for_date(day):
                    activities.append(_activity_to_dict(act))
            activities.sort(key=lambda a: a["date"], reverse=True)

            # Last 7 training entries (user-logged)
            training = [
                {"date": str(t.date), "description": t.description}
                for t in repo.get_recent_training(days=7)
            ]

            # Nutrition for the last 7 days
            nutrition = []
            for i in range(7):
                day = end - timedelta(days=i)
                n = repo.get_daily_nutrition(day)
                if n.get("calories"):
                    nutrition.append({
                        "date": str(day),
                        "calories_kcal": n.get("calories"),
                        "protein_g": n.get("protein_g"),
                        "fat_g": n.get("fat_g"),
                        "carbs_g": n.get("carbs_g"),
                        "fiber_g": n.get("fiber_g"),
                    })

            # Weekly averages (same period as metrics)
            weekly = repo.get_weekly_stats(end)
            weekly_summary = {
                "sleep_avg_hours": weekly.get("sleep_avg_hours"),
                "sleep_avg_score": weekly.get("sleep_avg_score"),
                "steps_avg": weekly.get("steps_avg"),
                "steps_total": weekly.get("steps_total"),
                "active_calories_total": weekly.get("active_calories_total"),
            } if weekly else {}

            self._send_json({
                "generated_at": str(date.today()),
                "metrics_last_7_days": metrics,
                "activities_last_14_days": activities,
                "training_log_last_7_days": training,
                "nutrition_last_7_days": nutrition,
                "weekly_averages": weekly_summary,
            })

        def log_message(self, format, *args):  # noqa: A002
            pass  # Suppress default access log spam

    return ApiHandler


def start_api_server(port: int, api_key: str, repo) -> threading.Thread:
    """Start the read-only data API in a daemon thread.

    Args:
        port:    TCP port to listen on.
        api_key: Secret key callers must supply in Authorization: Bearer header.
        repo:    Repository instance to read data from.

    Returns:
        The daemon thread (already started).
    """
    handler = _make_handler(api_key, repo)
    server = HTTPServer(("0.0.0.0", port), handler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Garmin data API started on port %d", port)
    return thread
