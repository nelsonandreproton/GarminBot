"""Tests for src/integrations/outsystems_client.py (unit tests — no real API calls)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.integrations.outsystems_client import OutSystemsClient


def _make_client(base_url="https://example.outsystemscloud.com/NA_CS/rest/NA_API"):
    return OutSystemsClient(base_url=base_url)


# ---------------------------------------------------------------------------
# record_exists
# ---------------------------------------------------------------------------

class TestRecordExists:
    def test_returns_false_when_id_zero(self):
        """OutSystems returns HTTP 200 with Id:0 for a non-existent record, not 404."""
        client = _make_client()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"Id": 0}
        with patch("requests.get", return_value=resp) as mock_get:
            assert client.record_exists(date(2026, 7, 29)) is False
        mock_get.assert_called_once()

    def test_returns_true_when_id_present(self):
        client = _make_client()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"Id": 172, "Day": "2026-07-29"}
        with patch("requests.get", return_value=resp):
            assert client.record_exists(date(2026, 7, 29)) is True

    def test_sends_iso_date_as_query_param(self):
        client = _make_client()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"Id": 0}
        with patch("requests.get", return_value=resp) as mock_get:
            client.record_exists(date(2026, 7, 29))
        _, kwargs = mock_get.call_args
        assert kwargs["params"] == {"Day": "2026-07-29"}

    def test_raises_on_http_error(self):
        """A non-2xx response must raise so the caller can fail closed (no POST)."""
        client = _make_client()
        resp = MagicMock(status_code=500)
        resp.raise_for_status.side_effect = requests.HTTPError("500 server error")
        with patch("requests.get", return_value=resp):
            with pytest.raises(requests.HTTPError):
                client.record_exists(date(2026, 7, 29))

    def test_raises_on_connection_error(self):
        client = _make_client()
        with patch("requests.get", side_effect=requests.ConnectionError("unreachable")):
            with pytest.raises(requests.ConnectionError):
                client.record_exists(date(2026, 7, 29))

    def test_retries_three_times_on_connection_error(self):
        """Transient network errors are retryable (GET is idempotent)."""
        client = _make_client()
        with patch("requests.get", side_effect=requests.ConnectionError("unreachable")) as mock_get, \
             patch("time.sleep"):
            with pytest.raises(requests.ConnectionError):
                client.record_exists(date(2026, 7, 29))
        assert mock_get.call_count == 3

    def test_does_not_retry_on_http_error(self):
        """4xx/5xx responses must not be retried."""
        client = _make_client()
        resp = MagicMock(status_code=500)
        resp.raise_for_status.side_effect = requests.HTTPError("500 server error")
        with patch("requests.get", return_value=resp) as mock_get:
            with pytest.raises(requests.HTTPError):
                client.record_exists(date(2026, 7, 29))
        assert mock_get.call_count == 1

    @pytest.mark.parametrize("payload", [
        {},
        {"Id": None},
        {"Id": ""},
        {"Record": {"Id": 172}},
        [],
        "unexpected",
    ])
    def test_raises_on_unexpected_payload_shape(self, payload):
        """Fail closed on any payload shape other than {"Id": <int>} — CreateRecord
        has no update endpoint, so a false 'not exists' would create a permanent
        duplicate."""
        client = _make_client()
        resp = MagicMock(status_code=200)
        resp.json.return_value = payload
        with patch("requests.get", return_value=resp):
            with pytest.raises(ValueError):
                client.record_exists(date(2026, 7, 29))


# ---------------------------------------------------------------------------
# create_record
# ---------------------------------------------------------------------------

class TestCreateRecord:
    def test_posts_expected_payload(self):
        client = _make_client()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"Id": 173, "Day": "2026-07-29"}
        with patch("requests.post", return_value=resp) as mock_post:
            client.create_record(
                day=date(2026, 7, 29),
                active=500,
                rest=1600,
                food=2000,
                protein=150,
                carbs=200,
                fat=70,
                weight=85.5,
                steps=8000,
            )
        _, kwargs = mock_post.call_args
        assert kwargs["json"] == {
            "Day": "2026-07-29",
            "Active": 500,
            "Rest": 1600,
            "Food": 2000,
            "Protein": 150,
            "Carbs": 200,
            "Fat": 70,
            "Weight": 85.5,
            "Steps": 8000,
        }

    def test_raises_on_http_error_no_retry(self):
        """POST failures must raise (caller decides how to surface); no retry on create."""
        client = _make_client()
        resp = MagicMock(status_code=500)
        resp.raise_for_status.side_effect = requests.HTTPError("500 server error")
        with patch("requests.post", return_value=resp) as mock_post:
            with pytest.raises(requests.HTTPError):
                client.create_record(
                    day=date(2026, 7, 29), active=1, rest=1, food=1,
                    protein=1, carbs=1, fat=1, weight=1.0, steps=1,
                )
        assert mock_post.call_count == 1

    def test_raises_on_connection_error_no_retry(self):
        """Even transient network errors are never retried on create (avoid duplicate
        record if the timed-out POST actually succeeded server-side)."""
        client = _make_client()
        with patch("requests.post", side_effect=requests.ConnectionError("timeout")) as mock_post:
            with pytest.raises(requests.ConnectionError):
                client.create_record(
                    day=date(2026, 7, 29), active=1, rest=1, food=1,
                    protein=1, carbs=1, fat=1, weight=1.0, steps=1,
                )
        assert mock_post.call_count == 1

    def test_integer_fields_rounded_before_send(self):
        """Food/Protein/Carbs/Fat/Active/Rest/Steps are typed integer in the swagger."""
        client = _make_client()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"Id": 1}
        with patch("requests.post", return_value=resp) as mock_post:
            client.create_record(
                day=date(2026, 7, 29),
                active=500.6,
                rest=1600.2,
                food=2000.9,
                protein=150.4,
                carbs=200.6,
                fat=70.1,
                weight=85.53,
                steps=8000.0,
            )
        _, kwargs = mock_post.call_args
        payload = kwargs["json"]
        assert payload["Active"] == 501
        assert payload["Rest"] == 1600
        assert payload["Food"] == 2001
        assert payload["Protein"] == 150
        assert payload["Carbs"] == 201
        assert payload["Fat"] == 70
        assert payload["Steps"] == 8000
        assert isinstance(payload["Steps"], int)
