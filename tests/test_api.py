"""Tests for src/utils/api.py — read-only HTTP data API."""

from __future__ import annotations

import json
import socket
import threading
import time
from datetime import date, timedelta
from http.server import HTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.utils.api import _make_handler, start_api_server

# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

API_KEY = "test-secret-key"


def _make_metrics(d: date, **kwargs) -> SimpleNamespace:
    defaults = dict(
        date=d,
        sleep_hours=7.5,
        sleep_score=72,
        sleep_quality="good",
        sleep_deep_min=90,
        sleep_light_min=180,
        sleep_rem_min=60,
        sleep_awake_min=10,
        steps=8000,
        active_calories=400,
        resting_calories=1800,
        total_calories=2200,
        floors_ascended=10,
        resting_heart_rate=55,
        avg_stress=35,
        body_battery_high=85,
        body_battery_low=20,
        spo2_avg=97.5,
        weight_kg=75.0,
        intensity_moderate_min=30,
        intensity_vigorous_min=10,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_activity(d: date, name="Running") -> SimpleNamespace:
    return SimpleNamespace(
        date=d,
        name=name,
        type_key="running",
        duration_min=45,
        calories=350,
        distance_km=7.5,
    )


def _make_training(d: date, description="Corrida 5km") -> SimpleNamespace:
    return SimpleNamespace(date=d, description=description)


def _make_repo(metrics=None, activities=None, training=None, nutrition=None, weekly=None) -> MagicMock:
    repo = MagicMock()
    repo.get_metrics_range.return_value = metrics or []
    repo.get_garmin_activities_for_date.return_value = activities or []
    repo.get_recent_training.return_value = training or []
    repo.get_daily_nutrition.return_value = nutrition or {}
    repo.get_weekly_stats.return_value = weekly or {}
    return repo


def _free_port() -> int:
    """Find a free TCP port."""
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _start_test_server(repo, key=API_KEY, sync_fn=None) -> tuple[int, HTTPServer]:
    port = _free_port()
    handler = _make_handler(key, repo, sync_fn=sync_fn)
    server = HTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)  # let the server bind
    return port, server


def _get(port: int, path: str, key: str | None = API_KEY) -> tuple[int, dict | list]:
    import urllib.request

    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url)
    if key is not None:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post(port: int, path: str, key: str | None = API_KEY) -> tuple[int, dict | list]:
    import urllib.request

    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, data=b"", method="POST")
    if key is not None:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ------------------------------------------------------------------ #
# Authentication                                                       #
# ------------------------------------------------------------------ #


def test_missing_auth_returns_401():
    port, server = _start_test_server(_make_repo())
    try:
        status, body = _get(port, "/api/metrics", key=None)
        assert status == 401
        assert body["error"] == "unauthorized"
    finally:
        server.shutdown()


def test_wrong_key_returns_401():
    port, server = _start_test_server(_make_repo())
    try:
        status, body = _get(port, "/api/metrics", key="wrong-key")
        assert status == 401
    finally:
        server.shutdown()


def test_x_api_key_header_accepted():
    import urllib.request

    repo = _make_repo(metrics=[_make_metrics(date.today() - timedelta(days=1))])
    port, server = _start_test_server(repo)
    try:
        url = f"http://127.0.0.1:{port}/api/metrics"
        req = urllib.request.Request(url)
        req.add_header("X-API-Key", API_KEY)
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
    finally:
        server.shutdown()


def test_unknown_path_returns_404():
    port, server = _start_test_server(_make_repo())
    try:
        status, body = _get(port, "/api/nonexistent")
        assert status == 404
    finally:
        server.shutdown()


# ------------------------------------------------------------------ #
# GET /api/metrics                                                     #
# ------------------------------------------------------------------ #


def test_metrics_returns_list():
    today = date.today()
    rows = [_make_metrics(today - timedelta(days=i)) for i in range(3)]
    port, server = _start_test_server(_make_repo(metrics=rows))
    try:
        status, body = _get(port, "/api/metrics?days=3")
        assert status == 200
        assert isinstance(body, list)
        assert len(body) == 3
    finally:
        server.shutdown()


def test_metrics_contains_expected_fields():
    d = date.today() - timedelta(days=1)
    rows = [_make_metrics(d, body_battery_high=90, sleep_hours=8.0)]
    port, server = _start_test_server(_make_repo(metrics=rows))
    try:
        _, body = _get(port, "/api/metrics")
        item = body[0]
        assert item["body_battery_high"] == 90
        assert item["sleep_hours"] == 8.0
        assert item["date"] == str(d)
    finally:
        server.shutdown()


def test_metrics_days_capped_at_30():
    """days param > 30 should be silently capped."""
    repo = _make_repo()
    port, server = _start_test_server(repo)
    try:
        _get(port, "/api/metrics?days=999")
        # Verify repo was called with a range of at most 30 days
        call_args = repo.get_metrics_range.call_args
        start, end = call_args[0]
        assert (end - start).days <= 29  # inclusive range = days - 1
    finally:
        server.shutdown()


def test_metrics_invalid_days_uses_default():
    repo = _make_repo()
    port, server = _start_test_server(repo)
    try:
        status, _ = _get(port, "/api/metrics?days=abc")
        assert status == 200  # falls back to default=7, no crash
    finally:
        server.shutdown()


# ------------------------------------------------------------------ #
# GET /api/activities                                                  #
# ------------------------------------------------------------------ #


def test_activities_returns_list():
    today = date.today()
    acts = [_make_activity(today - timedelta(days=i)) for i in range(3)]
    repo = _make_repo(activities=acts)
    port, server = _start_test_server(repo)
    try:
        status, body = _get(port, "/api/activities?days=3")
        assert status == 200
        assert isinstance(body, list)
        # repo.get_garmin_activities_for_date returns same list each call; 3 days × len(acts)
        assert len(body) == 3 * len(acts)
    finally:
        server.shutdown()


def test_activities_sorted_desc():
    """Activities should be sorted newest-first."""
    d1 = date(2025, 1, 1)
    d2 = date(2025, 1, 3)
    repo = MagicMock()
    repo.get_garmin_activities_for_date.side_effect = lambda day: (
        [_make_activity(day)] if day in (d1, d2) else []
    )
    repo.get_recent_training.return_value = []
    port, server = _start_test_server(repo)
    try:
        _, body = _get(port, "/api/activities?days=5")
        dates = [a["date"] for a in body if a["date"] in (str(d1), str(d2))]
        assert dates == sorted(dates, reverse=True)
    finally:
        server.shutdown()


def test_activities_contains_expected_fields():
    d = date.today() - timedelta(days=1)
    repo = _make_repo(activities=[_make_activity(d, name="Cycling")])
    port, server = _start_test_server(repo)
    try:
        _, body = _get(port, "/api/activities?days=2")
        item = next(a for a in body if a["name"] == "Cycling")
        assert item["type"] == "running"
        assert item["duration_min"] == 45
        assert item["distance_km"] == 7.5
    finally:
        server.shutdown()


# ------------------------------------------------------------------ #
# GET /api/summary                                                     #
# ------------------------------------------------------------------ #


def test_summary_structure():
    today = date.today()
    repo = _make_repo(
        metrics=[_make_metrics(today - timedelta(days=i)) for i in range(7)],
        activities=[_make_activity(today - timedelta(days=1))],
        training=[_make_training(today - timedelta(days=2))],
    )
    port, server = _start_test_server(repo)
    try:
        status, body = _get(port, "/api/summary")
        assert status == 200
        assert "metrics_last_7_days" in body
        assert "activities_last_14_days" in body
        assert "training_log_last_7_days" in body
        assert "nutrition_last_7_days" in body
        assert "weekly_averages" in body
        assert "generated_at" in body
    finally:
        server.shutdown()


def test_metrics_includes_floors_and_total_calories():
    d = date.today() - timedelta(days=1)
    rows = [_make_metrics(d, floors_ascended=15, total_calories=2300, resting_calories=1900)]
    port, server = _start_test_server(_make_repo(metrics=rows))
    try:
        _, body = _get(port, "/api/metrics")
        item = body[0]
        assert item["floors_ascended"] == 15
        assert item["total_calories"] == 2300
        assert item["resting_calories"] == 1900
    finally:
        server.shutdown()


def test_summary_nutrition_included():
    today = date.today()
    nutrition_data = {
        "calories": 1750.0, "protein_g": 167.0, "fat_g": 67.0,
        "carbs_g": 107.0, "fiber_g": 12.0,
    }
    repo = _make_repo(nutrition=nutrition_data)
    port, server = _start_test_server(repo)
    try:
        _, body = _get(port, "/api/summary")
        # nutrition_last_7_days has one entry per day that has calories
        entries = body["nutrition_last_7_days"]
        assert len(entries) == 7
        assert entries[0]["calories_kcal"] == 1750.0
        assert entries[0]["protein_g"] == 167.0
    finally:
        server.shutdown()


def test_summary_weekly_averages_included():
    weekly_data = {
        "sleep_avg_hours": 7.8, "sleep_avg_score": 75,
        "steps_avg": 8500, "steps_total": 59500, "active_calories_total": 2800,
    }
    repo = _make_repo(weekly=weekly_data)
    port, server = _start_test_server(repo)
    try:
        _, body = _get(port, "/api/summary")
        wa = body["weekly_averages"]
        assert wa["sleep_avg_hours"] == 7.8
        assert wa["steps_avg"] == 8500
    finally:
        server.shutdown()


def test_summary_training_fields():
    d = date.today() - timedelta(days=1)
    repo = _make_repo(training=[_make_training(d, description="Leg day")])
    port, server = _start_test_server(repo)
    try:
        _, body = _get(port, "/api/summary")
        entries = body["training_log_last_7_days"]
        assert len(entries) == 1
        assert entries[0]["description"] == "Leg day"
        assert entries[0]["date"] == str(d)
    finally:
        server.shutdown()


def test_summary_empty_data():
    port, server = _start_test_server(_make_repo())
    try:
        status, body = _get(port, "/api/summary")
        assert status == 200
        assert body["metrics_last_7_days"] == []
        assert body["activities_last_14_days"] == []
        assert body["training_log_last_7_days"] == []
    finally:
        server.shutdown()


# ------------------------------------------------------------------ #
# start_api_server helper                                              #
# ------------------------------------------------------------------ #


def test_start_api_server_returns_thread():
    port = _free_port()
    t = start_api_server(port, API_KEY, _make_repo())
    try:
        assert t.is_alive()
        # Verify it's reachable
        status, _ = _get(port, "/api/summary")
        assert status == 200
    finally:
        pass  # daemon thread; no explicit shutdown needed in tests


# ------------------------------------------------------------------ #
# POST /api/sync                                                       #
# ------------------------------------------------------------------ #


def test_sync_blocks_and_returns_metrics():
    """sync_fn is called synchronously; response contains yesterday's data."""
    yesterday = date.today() - timedelta(days=1)
    row = _make_metrics(yesterday, steps=12000, sleep_hours=8.0)

    repo = _make_repo()
    repo.get_metrics_by_date.return_value = row

    calls = []

    def fake_sync():
        calls.append(1)

    port, server = _start_test_server(repo, sync_fn=fake_sync)
    try:
        status, body = _post(port, "/api/sync")
        assert status == 200
        assert len(calls) == 1, "sync_fn must be called exactly once"
        assert body["steps"] == 12000
        assert body["sleep_hours"] == 8.0
        assert body["date"] == str(yesterday)
    finally:
        server.shutdown()


def test_sync_without_auth_returns_401():
    port, server = _start_test_server(_make_repo(), sync_fn=lambda: None)
    try:
        status, body = _post(port, "/api/sync", key=None)
        assert status == 401
    finally:
        server.shutdown()


def test_sync_not_configured_returns_503():
    """When no sync_fn is provided, POST /api/sync returns 503."""
    port, server = _start_test_server(_make_repo())  # no sync_fn
    try:
        status, body = _post(port, "/api/sync")
        assert status == 503
        assert "not configured" in body["error"]
    finally:
        server.shutdown()


def test_sync_fn_exception_returns_500():
    def failing_sync():
        raise RuntimeError("Garmin unreachable")

    port, server = _start_test_server(_make_repo(), sync_fn=failing_sync)
    try:
        status, body = _post(port, "/api/sync")
        assert status == 500
        assert "Garmin unreachable" in body["error"]
    finally:
        server.shutdown()


def test_sync_no_data_after_sync_returns_404():
    repo = _make_repo()
    repo.get_metrics_by_date.return_value = None  # sync ran but no row saved

    port, server = _start_test_server(repo, sync_fn=lambda: None)
    try:
        status, body = _post(port, "/api/sync")
        assert status == 404
    finally:
        server.shutdown()


def test_post_unknown_path_returns_404():
    port, server = _start_test_server(_make_repo())
    try:
        status, body = _post(port, "/api/nonexistent")
        assert status == 404
    finally:
        server.shutdown()


def test_sync_fn_is_called_before_response():
    """Verify the sync completes (side effect visible) before the response is read."""
    yesterday = date.today() - timedelta(days=1)
    row = _make_metrics(yesterday)

    state = {"synced": False}

    def fake_sync():
        state["synced"] = True

    repo = _make_repo()
    repo.get_metrics_by_date.return_value = row

    port, server = _start_test_server(repo, sync_fn=fake_sync)
    try:
        status, _ = _post(port, "/api/sync")
        assert status == 200
        assert state["synced"] is True
    finally:
        server.shutdown()
