"""Garmin Connect authentication with token persistence and retry logic."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import garminconnect
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

TOKEN_FILE = Path("./data/garmin_tokens.json")


def _on_retry(retry_state) -> None:
    logger.warning(
        "Garmin auth attempt %d failed: %s",
        retry_state.attempt_number,
        retry_state.outcome.exception(),
    )


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=4, max=30),
    before_sleep=_on_retry,
    reraise=True,
)
def create_garmin_client(email: str, password: str) -> garminconnect.Garmin:
    """Authenticate with Garmin Connect and return an authenticated client.

    Attempts to reuse a persisted OAuth2 token before falling back to
    full username/password login. Token is saved to disk for reuse.

    Args:
        email: Garmin account email.
        password: Garmin account password.

    Returns:
        Authenticated Garmin client instance.

    Raises:
        Exception: If authentication fails after 3 attempts.
    """
    client = garminconnect.Garmin(email, password)

    if TOKEN_FILE.exists():
        try:
            token_data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            client.login(token_data)
            logger.info("Garmin: reused existing token")
            return client
        except Exception as exc:
            logger.warning("Garmin: saved token invalid (%s), re-authenticating", exc)
            TOKEN_FILE.unlink(missing_ok=True)

    client.login()
    _save_token(client)
    logger.info("Garmin: authenticated with email/password")
    return client


def _save_token(client: garminconnect.Garmin) -> None:
    """Persist the OAuth2 token to disk."""
    try:
        token_data = client.garth.dumps()
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(token_data, encoding="utf-8")
        # Restrict file permissions on Unix-like systems
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except OSError:
            pass
        logger.debug("Garmin token saved to %s", TOKEN_FILE)
    except Exception as exc:
        logger.warning("Could not save Garmin token: %s", exc)


def invalidate_token() -> None:
    """Delete the persisted token, forcing re-authentication on next login."""
    TOKEN_FILE.unlink(missing_ok=True)
    logger.info("Garmin token invalidated")
