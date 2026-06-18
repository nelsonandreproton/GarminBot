"""FatSecret platform API client.

Uses OAuth1 with query-string signature (SIGNATURE_TYPE_QUERY) as required by
FatSecret's REST API. Token is one-time setup via scripts/fatsecret_probe.py;
this client only reads the saved token file.

OAuth note: FatSecret requires oauth_* parameters in the query string, not the
Authorization header. Omitting SIGNATURE_TYPE_QUERY causes
"Missing required parameter: oauth_consumer_key".
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from pathlib import Path

from oauthlib.oauth1 import SIGNATURE_TYPE_QUERY
from requests_oauthlib import OAuth1Session
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_BASE_URL = "https://platform.fatsecret.com/rest/server.api"
_EPOCH = datetime.date(1970, 1, 1)

# SIGNATURE_TYPE_QUERY puts oauth_consumer_key / oauth_token / oauth_signature in
# the request URL. On any HTTP error the full signed URL ends up in the HTTPError
# string — so we MUST redact those params before logging the exception.
_OAUTH_PARAM_RE = re.compile(r"(oauth_\w+)=[^&\s]+")


def _redact(text: object) -> str:
    """Strip OAuth credentials from any text before it reaches the logs."""
    return _OAUTH_PARAM_RE.sub(r"\1=REDACTED", str(text))


class FatSecretAuthError(Exception):
    """Raised on a FatSecret error envelope that should NOT be retried.

    Covers auth/token errors (codes 3, 6-9) AND quota/rate-limit errors
    (codes 4 "Request limit reached" and 5 "Rate limit exceeded") — both must
    fail fast (project hard rule: never retry on rate-limit).
    """


def _is_rate_limit(exc: BaseException) -> bool:
    """Return True if the exception represents an HTTP 429 rate-limit response."""
    if "429" in str(exc):
        return True
    # Also check the response attribute on HTTPError (requests library)
    try:
        from requests.exceptions import HTTPError
        if isinstance(exc, HTTPError) and exc.response is not None:
            return exc.response.status_code == 429
    except ImportError:
        pass
    return False


def _is_auth_error(exc: BaseException) -> bool:
    """Return True if the exception is a FatSecret auth error (token invalid etc.)."""
    return isinstance(exc, FatSecretAuthError)


def _on_retry(retry_state) -> None:
    logger.warning(
        "FatSecret API attempt %d failed: %s",
        retry_state.attempt_number,
        _redact(retry_state.outcome.exception()),
    )


class FatSecretClient:
    """Minimal FatSecret platform client for reading the food diary.

    Token setup (one-time) is handled externally via scripts/fatsecret_probe.py.
    If the token file is missing or invalid, all methods return [] gracefully.
    """

    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        token_file: str = "data/fatsecret_token.json",
    ) -> None:
        self._consumer_key = consumer_key
        self._consumer_secret = consumer_secret
        self._token_file = token_file
        self._token: dict | None = self._load_token()

    def _load_token(self) -> dict | None:
        """Load OAuth token from JSON file.

        Returns the token dict on success, None if file is missing or keys absent.
        Logs a warning (not an exception) so the bot starts without FatSecret.
        """
        path = Path(self._token_file)
        if not path.exists():
            logger.warning(
                "FatSecret token file not found: %s — nutrition diary sync disabled",
                self._token_file,
            )
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("FatSecret token file unreadable (%s): %s", self._token_file, exc)
            return None
        if "access_token" not in data or "access_token_secret" not in data:
            logger.warning(
                "FatSecret token file %s is missing required keys (access_token / access_token_secret)",
                self._token_file,
            )
            return None
        return data

    @retry(
        # Do NOT retry rate-limit errors (project hard rule) or auth errors
        # (retrying a bad token is pointless — token setup is one-time via script).
        retry=retry_if_exception(lambda exc: not _is_rate_limit(exc) and not _is_auth_error(exc)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        before_sleep=_on_retry,
        reraise=True,
    )
    def get_food_entries(self, day: datetime.date) -> list[dict]:
        """Fetch raw food diary entries for the given date from FatSecret.

        Returns:
            List of raw entry dicts (string fields as-is from the API).
            Returns [] if the client is unconfigured (no token).
            Returns [] if the day has no entries.

        Raises:
            FatSecretAuthError: For API error envelopes with auth codes 2–9.
            requests.exceptions.HTTPError: For HTTP-level errors including 429.
        """
        if self._token is None:
            logger.warning("FatSecret: no token configured — skipping diary fetch for %s", day)
            return []

        date_int = (day - _EPOCH).days

        params = {
            "method": "food_entries.get",
            "format": "json",
            "date": date_int,
        }

        # Context-managed so the connection pool is released after each call
        # (/hoje can fire repeatedly — a leaked Session would exhaust sockets).
        with OAuth1Session(
            self._consumer_key,
            client_secret=self._consumer_secret,
            resource_owner_key=self._token["access_token"],
            resource_owner_secret=self._token["access_token_secret"],
            signature_type=SIGNATURE_TYPE_QUERY,
        ) as session:
            resp = session.get(_BASE_URL, params=params)
            resp.raise_for_status()  # raises HTTPError for 4xx/5xx, including 429
            data = resp.json()

        # Check for FatSecret error envelope (returned with HTTP 200)
        if "error" in data:
            err = data["error"]
            code = err.get("code")
            message = err.get("message", "unknown")
            # Codes 2-9 are non-retryable: auth/token errors AND quota errors
            # (4 = request limit reached, 5 = rate limit exceeded). Both must
            # fail fast — never retry on rate-limit (project hard rule).
            if code in range(2, 10):
                raise FatSecretAuthError(
                    f"FatSecret non-retryable error (code {code}): {message}"
                )
            # Other error codes: raise generic so tenacity can retry
            raise RuntimeError(f"FatSecret API error (code {code}): {message}")

        from .fatsecret_mapper import normalize_food_entries
        return normalize_food_entries(data.get("food_entries"))
