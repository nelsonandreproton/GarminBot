"""Tests for BearerAuthMiddleware — pure ASGI middleware, no FastMCP dependency.

These tests use a trivial dummy ASGI app wrapped with BearerAuthMiddleware.
Plain TestClient (NOT the with-form) is used so no lifespan scope is emitted,
which means the dummy only needs to handle 'http' scopes.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from starlette.requests import Request
from starlette.responses import PlainTextResponse


# ---------------------------------------------------------------------------
# Dummy ASGI app — returns 200 "OK" for any HTTP request
# ---------------------------------------------------------------------------

async def _dummy_app(scope, receive, send):
    """Minimal ASGI app that responds 200 OK to any http request."""
    assert scope["type"] == "http"
    response = PlainTextResponse("OK")
    await response(scope, receive, send)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(token: str):
    """Wrap dummy app with BearerAuthMiddleware and return a TestClient."""
    from src.mcp.auth import BearerAuthMiddleware

    wrapped = BearerAuthMiddleware(_dummy_app, expected_token=token)
    return TestClient(wrapped, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Construction-time guards
# ---------------------------------------------------------------------------

class TestBearerAuthMiddlewareConstruction:
    def test_empty_string_token_raises_value_error(self):
        from src.mcp.auth import BearerAuthMiddleware

        with pytest.raises(ValueError, match="expected_token"):
            BearerAuthMiddleware(_dummy_app, expected_token="")

    def test_none_token_raises_value_error(self):
        from src.mcp.auth import BearerAuthMiddleware

        with pytest.raises(ValueError, match="expected_token"):
            BearerAuthMiddleware(_dummy_app, expected_token=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 401 cases
# ---------------------------------------------------------------------------

class TestBearerAuthMiddleware401:
    CORRECT_TOKEN = "supersecrettoken"

    def test_no_authorization_header_returns_401(self):
        client = _make_client(self.CORRECT_TOKEN)
        response = client.get("/")
        assert response.status_code == 401

    def test_wrong_token_returns_401(self):
        client = _make_client(self.CORRECT_TOKEN)
        response = client.get("/", headers={"Authorization": "Bearer wrongtoken"})
        assert response.status_code == 401

    def test_wrong_scheme_basic_returns_401(self):
        client = _make_client(self.CORRECT_TOKEN)
        response = client.get("/", headers={"Authorization": "Basic abc123"})
        assert response.status_code == 401

    def test_bearer_prefix_only_no_token_returns_401(self):
        """'Bearer ' with no token part should be rejected."""
        client = _make_client(self.CORRECT_TOKEN)
        response = client.get("/", headers={"Authorization": "Bearer "})
        assert response.status_code == 401

    def test_401_body_does_not_echo_presented_token(self):
        """Security: the response body must NOT contain the token the caller sent."""
        presented = "my-secret-token-1234"
        client = _make_client("different-correct-token")
        response = client.get("/", headers={"Authorization": f"Bearer {presented}"})
        assert response.status_code == 401
        assert presented not in response.text


# ---------------------------------------------------------------------------
# 200 case
# ---------------------------------------------------------------------------

class TestBearerAuthMiddleware200:
    CORRECT_TOKEN = "supersecrettoken"

    def test_correct_bearer_token_passes_through_to_app(self):
        client = _make_client(self.CORRECT_TOKEN)
        response = client.get("/", headers={"Authorization": f"Bearer {self.CORRECT_TOKEN}"})
        assert response.status_code == 200
        assert response.text == "OK"

    def test_correct_token_case_insensitive_bearer_scheme(self):
        """Scheme 'bearer' (lowercase) should also be accepted per RFC 7235 convention."""
        client = _make_client(self.CORRECT_TOKEN)
        response = client.get("/", headers={"Authorization": f"bearer {self.CORRECT_TOKEN}"})
        assert response.status_code == 200

    def test_correct_token_mixed_case_bearer_scheme(self):
        """Scheme 'BEARER' (uppercase) should also be accepted."""
        client = _make_client(self.CORRECT_TOKEN)
        response = client.get("/", headers={"Authorization": f"BEARER {self.CORRECT_TOKEN}"})
        assert response.status_code == 200

    def test_token_comparison_is_case_sensitive(self):
        """Token itself must match exactly — wrong case → 401."""
        client = _make_client("SecretToken")
        response = client.get("/", headers={"Authorization": "Bearer secrettoken"})
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Scope-type handling: lifespan passes through; websocket is rejected
# ---------------------------------------------------------------------------

class TestScopeTypeHandling:
    """Verify that only 'lifespan' bypasses auth; websocket is rejected."""

    @pytest.mark.asyncio
    async def test_lifespan_scope_passes_through_to_app(self):
        """lifespan scope must reach the wrapped app so the server can start."""
        from src.mcp.auth import BearerAuthMiddleware

        received_scopes = []

        async def recording_app(scope, receive, send):
            received_scopes.append(scope["type"])
            # Minimal lifespan handshake so ASGI compliant.
            if scope["type"] == "lifespan":
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                message = await receive()
                if message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})

        middleware = BearerAuthMiddleware(recording_app, expected_token="tok")

        startup_done = False
        shutdown_done = False
        events = [
            {"type": "lifespan.startup"},
            {"type": "lifespan.shutdown"},
        ]
        event_iter = iter(events)

        async def receive():
            return next(event_iter)

        sent_messages = []

        async def send(msg):
            nonlocal startup_done, shutdown_done
            sent_messages.append(msg)
            if msg["type"] == "lifespan.startup.complete":
                startup_done = True
            if msg["type"] == "lifespan.shutdown.complete":
                shutdown_done = True

        await middleware({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)

        assert "lifespan" in received_scopes, "lifespan scope must reach wrapped app"
        assert startup_done, "lifespan.startup.complete must be sent"
        assert shutdown_done, "lifespan.shutdown.complete must be sent"

    @pytest.mark.asyncio
    async def test_websocket_scope_does_not_reach_wrapped_app(self):
        """websocket scope must be rejected — not forwarded to the inner app."""
        from src.mcp.auth import BearerAuthMiddleware

        app_was_called = False

        async def recording_app(scope, receive, send):
            nonlocal app_was_called
            app_was_called = True

        middleware = BearerAuthMiddleware(recording_app, expected_token="tok")

        sent_messages = []

        async def receive():
            return {}

        async def send(msg):
            sent_messages.append(msg)

        scope = {
            "type": "websocket",
            "headers": [],
            "path": "/ws",
            "query_string": b"",
        }
        await middleware(scope, receive, send)

        assert not app_was_called, "websocket scope must NOT reach the wrapped app"
        # A websocket.close must have been sent to signal rejection.
        close_messages = [m for m in sent_messages if m.get("type") == "websocket.close"]
        assert close_messages, "websocket.close must be sent to reject the connection"
        assert close_messages[0].get("code") == 1008, "close code must be 1008 (policy violation)"
