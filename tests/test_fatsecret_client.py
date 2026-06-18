"""Tests for src/nutrition/fatsecret_client.py (unit tests — no real API calls)."""

from __future__ import annotations

import json
import logging
from datetime import date
from unittest.mock import MagicMock, mock_open, patch

import pytest

from src.nutrition.fatsecret_client import FatSecretAuthError, FatSecretClient

# ---------------------------------------------------------------------------
# Shared API fixture: verbatim single-entry envelope (from real API run)
# ---------------------------------------------------------------------------

SINGLE_ENTRY_RAW = {
    "calories": "121",
    "carbohydrate": "1.89",
    "date_int": "20622",
    "fat": "2.43",
    "fiber": "0.4",
    "food_entry_description": "0.3 serving Prozis 100% Vegan Protein Baunilha",
    "food_entry_id": "23984959963",
    "food_entry_name": "Prozis 100% Vegan Protein Baunilha",
    "food_id": "46844289",
    "meal": "Breakfast",
    "number_of_units": "0.300",
    "protein": "22.50",
    "saturated_fat": "0.570",
    "serving_id": "40105019",
    "sodium": "596",
    "sugar": "0.09",
}

SINGLE_ENTRY_ENVELOPE = {"food_entries": {"food_entry": SINGLE_ENTRY_RAW}}

MULTI_ENTRY_ENVELOPE = {
    "food_entries": {
        "food_entry": [
            SINGLE_ENTRY_RAW,
            {
                "calories": "200",
                "carbohydrate": "40.00",
                "fat": "1.00",
                "food_entry_id": "99999",
                "food_entry_name": "Oats",
                "food_id": "111",
                "meal": "Breakfast",
                "number_of_units": "1.000",
                "protein": "5.00",
            },
        ]
    }
}

NULL_ENTRIES_ENVELOPE = {"food_entries": None}

AUTH_ERROR_ENVELOPE = {"error": {"code": 3, "message": "Token is invalid"}}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_TOKEN = {"access_token": "tok123", "access_token_secret": "sec456"}


def _make_client(token_data=VALID_TOKEN, token_file="data/fatsecret_token.json"):
    """Build a FatSecretClient with a mocked token file load."""
    client = FatSecretClient(
        consumer_key="ck",
        consumer_secret="cs",
        token_file=token_file,
    )
    # Inject token directly so we don't need file I/O for most tests
    client._token = token_data
    return client


def _mock_response(json_data: dict, status_code: int = 200):
    """Return a mock requests.Response-like object."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    if status_code >= 400:
        from requests.exceptions import HTTPError
        mock_resp.raise_for_status.side_effect = HTTPError(
            response=mock_resp
        )
    else:
        mock_resp.raise_for_status.return_value = None
    return mock_resp


# ---------------------------------------------------------------------------
# Token loading
# ---------------------------------------------------------------------------


class TestLoadToken:
    def test_missing_token_file_marks_unconfigured(self, tmp_path):
        client = FatSecretClient("ck", "cs", token_file=str(tmp_path / "missing.json"))
        assert client._token is None

    def test_missing_token_file_logs_warning(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING):
            FatSecretClient("ck", "cs", token_file=str(tmp_path / "missing.json"))
        assert any("token" in r.message.lower() for r in caplog.records)

    def test_valid_token_file_loads_token(self, tmp_path):
        token_file = tmp_path / "token.json"
        token_file.write_text(json.dumps(VALID_TOKEN))
        client = FatSecretClient("ck", "cs", token_file=str(token_file))
        assert client._token == VALID_TOKEN

    def test_token_file_missing_key_marks_unconfigured(self, tmp_path, caplog):
        token_file = tmp_path / "token.json"
        token_file.write_text(json.dumps({"access_token": "only_one_key"}))
        with caplog.at_level(logging.WARNING):
            client = FatSecretClient("ck", "cs", token_file=str(token_file))
        assert client._token is None
        assert any("token" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Graceful degradation: unconfigured client
# ---------------------------------------------------------------------------


class TestUnconfiguredClient:
    def test_returns_empty_list_when_no_token(self, tmp_path):
        client = FatSecretClient("ck", "cs", token_file=str(tmp_path / "missing.json"))
        result = client.get_food_entries(date(2026, 1, 1))
        assert result == []

    def test_logs_warning_when_no_token(self, tmp_path, caplog):
        client = FatSecretClient("ck", "cs", token_file=str(tmp_path / "missing.json"))
        with caplog.at_level(logging.WARNING):
            client.get_food_entries(date(2026, 1, 1))
        assert any("token" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# get_food_entries: normalization
# ---------------------------------------------------------------------------


class TestGetFoodEntriesNormalization:
    @patch("src.nutrition.fatsecret_client.OAuth1Session")
    def test_single_entry_dict_returns_list_of_one(self, mock_oauth_cls):
        client = _make_client()
        mock_session = MagicMock()
        mock_oauth_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = _mock_response(SINGLE_ENTRY_ENVELOPE)

        result = client.get_food_entries(date(2026, 1, 1))

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["food_entry_name"] == "Prozis 100% Vegan Protein Baunilha"

    @patch("src.nutrition.fatsecret_client.OAuth1Session")
    def test_multi_entry_list_returns_full_list(self, mock_oauth_cls):
        client = _make_client()
        mock_session = MagicMock()
        mock_oauth_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = _mock_response(MULTI_ENTRY_ENVELOPE)

        result = client.get_food_entries(date(2026, 1, 1))

        assert isinstance(result, list)
        assert len(result) == 2

    @patch("src.nutrition.fatsecret_client.OAuth1Session")
    def test_null_food_entries_returns_empty(self, mock_oauth_cls):
        client = _make_client()
        mock_session = MagicMock()
        mock_oauth_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = _mock_response(NULL_ENTRIES_ENVELOPE)

        result = client.get_food_entries(date(2026, 1, 1))

        assert result == []


# ---------------------------------------------------------------------------
# get_food_entries: error envelope
# ---------------------------------------------------------------------------


class TestGetFoodEntriesErrors:
    @patch("src.nutrition.fatsecret_client.OAuth1Session")
    def test_auth_error_envelope_raises(self, mock_oauth_cls):
        client = _make_client()
        mock_session = MagicMock()
        mock_oauth_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = _mock_response(AUTH_ERROR_ENVELOPE)

        with pytest.raises(FatSecretAuthError, match=r"FatSecret non-retryable error \(code 3\)"):
            client.get_food_entries(date(2026, 1, 1))

    @patch("src.nutrition.fatsecret_client.OAuth1Session")
    def test_http_429_raises_without_retry(self, mock_oauth_cls):
        """Rate-limit must NOT be retried — raises immediately."""
        client = _make_client()
        mock_session = MagicMock()
        mock_oauth_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = _mock_response({}, status_code=429)

        from requests.exceptions import HTTPError
        with pytest.raises(HTTPError):
            client.get_food_entries(date(2026, 1, 1))

        # Only one HTTP call — no retry
        assert mock_session.get.call_count == 1


# ---------------------------------------------------------------------------
# Date encoding
# ---------------------------------------------------------------------------


class TestDateEncoding:
    @patch("src.nutrition.fatsecret_client.OAuth1Session")
    def test_date_encoded_as_days_since_epoch(self, mock_oauth_cls):
        """date_int must be days since 1970-01-01 (naive local calendar)."""
        client = _make_client()
        mock_session = MagicMock()
        mock_oauth_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = _mock_response(NULL_ENTRIES_ENVELOPE)

        test_date = date(1970, 1, 2)  # day 1 since epoch
        client.get_food_entries(test_date)

        call_kwargs = mock_session.get.call_args
        params = call_kwargs[1]["params"] if call_kwargs[1] else call_kwargs[0][1]
        assert params["date"] == 1

    @patch("src.nutrition.fatsecret_client.OAuth1Session")
    def test_date_epoch_is_zero(self, mock_oauth_cls):
        client = _make_client()
        mock_session = MagicMock()
        mock_oauth_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = _mock_response(NULL_ENTRIES_ENVELOPE)

        client.get_food_entries(date(1970, 1, 1))

        call_kwargs = mock_session.get.call_args
        params = call_kwargs[1]["params"] if call_kwargs[1] else call_kwargs[0][1]
        assert params["date"] == 0


# ---------------------------------------------------------------------------
# _redact helper (security: OAuth credentials must not appear in logs)
# ---------------------------------------------------------------------------


class TestRedact:
    def test_redact_strips_oauth_params(self):
        from src.nutrition.fatsecret_client import _redact

        url = (
            "https://platform.fatsecret.com/rest/server.api"
            "?oauth_consumer_key=MYKEY&oauth_token=MYTOKEN"
            "&oauth_signature=MYSIG&format=json"
        )
        redacted = _redact(url)
        assert "MYKEY" not in redacted
        assert "MYTOKEN" not in redacted
        assert "MYSIG" not in redacted
        # Non-oauth params are preserved
        assert "format=json" in redacted

    def test_redact_replaces_with_redacted_marker(self):
        from src.nutrition.fatsecret_client import _redact

        text = "oauth_consumer_key=abc123&other=value"
        redacted = _redact(text)
        assert "oauth_consumer_key=REDACTED" in redacted
        assert "abc123" not in redacted

    def test_redact_leaves_non_oauth_text_unchanged(self):
        from src.nutrition.fatsecret_client import _redact

        text = "some innocent log message with no oauth params"
        assert _redact(text) == text
