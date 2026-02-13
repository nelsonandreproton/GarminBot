"""Minimal HTTP health check server for external monitoring (e.g. UptimeRobot)."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logger = logging.getLogger(__name__)

_start_time = time.monotonic()


def _make_handler(get_status: callable):
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            status = get_status()
            code = 200 if status.get("ok") else 503
            body = json.dumps(status, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass  # Suppress default access log spam

    return HealthHandler


def start_health_server(port: int, get_status: callable) -> threading.Thread:
    """Start the health check HTTP server in a daemon thread.

    Args:
        port: TCP port to listen on.
        get_status: Callable that returns a status dict with at least {"ok": bool}.

    Returns:
        The daemon thread (already started).
    """
    handler = _make_handler(get_status)
    server = HTTPServer(("0.0.0.0", port), handler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health check server started on port %d", port)
    return thread
