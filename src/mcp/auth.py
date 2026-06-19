"""Bearer token authentication middleware for the GarminBot MCP HTTP server.

Pure ASGI middleware — does NOT use BaseHTTPMiddleware to avoid response-
buffering that breaks SSE/streaming.  Only 'http' scopes are gated; 'lifespan'
passes through unauthenticated so the server can start; all other scope types
(notably 'websocket') are rejected rather than forwarded.
"""

from __future__ import annotations

import hmac


# Minimal ASGI 401 response bytes — pre-built for performance.
_UNAUTHORIZED_BODY = b"Unauthorized"
_UNAUTHORIZED_HEADERS = [
    (b"content-type", b"text/plain; charset=utf-8"),
    (b"content-length", b"12"),
    (b"www-authenticate", b'Bearer realm="GarminBot MCP"'),
]


class BearerAuthMiddleware:
    """ASGI middleware that enforces Bearer token authentication on HTTP scopes.

    Scope routing:
    - 'lifespan' — passed through unauthenticated (required for server startup).
    - 'http'     — bearer-token gated; responds 401 if missing or invalid.
    - any other  — rejected without forwarding to the inner app. 'websocket'
                   scopes receive a websocket.close (code 1008 policy violation);
                   truly unknown scope types are silently dropped (connection drops).

    Fail-closed: raises ValueError at construction time if expected_token is
    empty or None, so a missing config is never silently treated as open access.
    """

    def __init__(self, app, *, expected_token: str) -> None:
        if not expected_token:
            raise ValueError(
                "expected_token must be a non-empty string; "
                "refusing to start with an empty token (fail-closed)."
            )
        self._app = app
        self._expected = expected_token.encode()

    async def __call__(self, scope, receive, send) -> None:
        scope_type = scope["type"]

        # lifespan must reach the inner app so the server can start.
        if scope_type == "lifespan":
            await self._app(scope, receive, send)
            return

        # Unknown/unsupported scope (e.g. websocket) — refuse rather than forward
        # unauthenticated.  Websocket gets a proper close frame; anything else is
        # silently dropped (the connection dies without a response).
        if scope_type != "http":
            if scope_type == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return

        # HTTP path: existing bearer-token check.
        if not self._is_authorized(scope):
            await self._send_401(send)
            return

        await self._app(scope, receive, send)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_authorized(self, scope) -> bool:
        """Return True iff the request carries the correct Bearer token."""
        auth_value = _get_header(scope["headers"], b"authorization")
        if auth_value is None:
            return False

        # Scheme is case-insensitive per RFC 7235; compare lowercased prefix.
        lowered = auth_value.lower()
        if not lowered.startswith(b"bearer "):
            return False

        # Slice the token from the ORIGINAL (un-lowercased) value to preserve
        # case — tokens are case-sensitive.
        presented_token = auth_value[7:]  # len("bearer ") == 7
        if not presented_token:
            return False

        # Constant-time comparison to resist timing attacks.
        return hmac.compare_digest(presented_token, self._expected)

    @staticmethod
    async def _send_401(send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": _UNAUTHORIZED_HEADERS,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": _UNAUTHORIZED_BODY,
                "more_body": False,
            }
        )


def _get_header(headers: list[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    """Return the first header value matching *name* (already lower-cased in ASGI)."""
    for key, value in headers:
        if key == name:
            return value
    return None
