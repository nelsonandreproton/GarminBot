"""Client for the Nelson's OutSystems daily-record API.

Endpoints (no authentication — confirmed against the live swagger):

  GET  /GetRecordPerday?Day=YYYY-MM-DD  — returns {"Id": 0, ...} when no record
                                           exists for that day (HTTP 200, not 404)
  POST /CreateRecord                    — creates a record; no update endpoint exists,
                                           so a record must never be created twice

Active/Rest/Food/Protein/Carbs/Fat/Steps are typed `integer` in the swagger;
Weight is `number`. Integer fields are rounded before sending.
"""

from __future__ import annotations

import logging
from datetime import date

import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10


def _is_retryable(exc: BaseException) -> bool:
    """Retry only on connection/timeout errors — never on 4xx/5xx HTTP responses."""
    if isinstance(exc, requests.HTTPError):
        return False
    return isinstance(exc, requests.RequestException)


class OutSystemsClient:
    """Thin wrapper over the OutSystems NA_API daily-record endpoints."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def record_exists(self, day: date) -> bool:
        """Return True if a record already exists for this day.

        A non-existent record is HTTP 200 with {"Id": 0} — NOT a 404.
        Raises on any HTTP error, connection failure, or unexpected payload
        shape so the caller can fail closed (never POST when existence can't
        be verified — CreateRecord has no update endpoint, so a false "not
        exists" here would create a permanent duplicate).
        """
        resp = requests.get(
            f"{self._base_url}/GetRecordPerday",
            params={"Day": day.isoformat()},
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or not isinstance(data.get("Id"), int):
            raise ValueError(f"Unexpected GetRecordPerday payload for {day}: {data!r}")
        return data["Id"] != 0

    def create_record(
        self,
        day: date,
        active: float,
        rest: float,
        food: float,
        protein: float,
        carbs: float,
        fat: float,
        weight: float,
        steps: float,
    ) -> None:
        """Create a new daily record. Never retried — CreateRecord has no update
        endpoint, so retrying a timed-out-but-succeeded POST would create a duplicate.
        """
        payload = {
            "Day": day.isoformat(),
            "Active": round(active),
            "Rest": round(rest),
            "Food": round(food),
            "Protein": round(protein),
            "Carbs": round(carbs),
            "Fat": round(fat),
            "Weight": weight,
            "Steps": round(steps),
        }
        resp = requests.post(
            f"{self._base_url}/CreateRecord",
            json=payload,
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
