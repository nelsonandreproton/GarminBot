"""Tests for the /tempo weather command: Open-Meteo client, formatter and handler."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests

from src.config import Config
from src.integrations import weather_client
from src.integrations.weather_client import fetch_weather, format_weather
from src.telegram.bot import TelegramBot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _forecast_json(**daily_overrides):
    daily = {
        "time": ["2026-03-14"],
        "temperature_2m_min": [9.4],
        "temperature_2m_mean": [14.2],
        "temperature_2m_max": [19.6],
        "apparent_temperature_min": [8.1],
        "apparent_temperature_max": [18.7],
        "precipitation_probability_max": [35],
        "precipitation_sum": [1.24],
        "uv_index_max": [5.6],
        "windspeed_10m_max": [22.4],
        "sunrise": ["2026-03-14T07:12"],
        "sunset": ["2026-03-14T19:01"],
    }
    daily.update(daily_overrides)
    return {"daily": daily, "hourly": {"relative_humidity_2m": [60, 70, None, 80]}}


def _aqi_json(aqi=None, pm25=None, pm10=None):
    return {
        "hourly": {
            "european_aqi": aqi if aqi is not None else [10, 25, None, 18],
            "pm2_5": pm25 if pm25 is not None else [5.0] * 24,
            "pm10": pm10 if pm10 is not None else [9.0] * 24,
        }
    }


def _response(payload, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status} error", response=resp)
    else:
        resp.raise_for_status.return_value = None
    return resp


def _fake_get(forecast=None, aqi=None):
    forecast = forecast if forecast is not None else _forecast_json()
    aqi = aqi if aqi is not None else _aqi_json()

    def _get(url, params=None, timeout=None):
        if url == weather_client.FORECAST_URL:
            return _response(forecast)
        if url == weather_client.AQI_URL:
            return _response(aqi)
        raise AssertionError(f"unexpected url {url}")

    return _get


@pytest.fixture(autouse=True)
def _no_backoff_sleep():
    """Make tenacity retries instant."""
    with patch("tenacity.nap.time.sleep"):
        yield


# ---------------------------------------------------------------------------
# fetch_weather
# ---------------------------------------------------------------------------

class TestFetchWeather:
    def test_fetch_weather_valid_payload_returns_parsed_fields(self):
        with patch("src.integrations.weather_client.requests.get", side_effect=_fake_get()):
            data = fetch_weather(38.7, -9.1)
        assert data["date"] == "2026-03-14"
        assert data["temp_min"] == 9.4
        assert data["temp_avg"] == 14.2
        assert data["temp_max"] == 19.6
        assert data["feels_min"] == 8.1
        assert data["feels_max"] == 18.7
        assert data["precip_probability"] == 35
        assert data["precip_sum"] == 1.24
        assert data["uv_index_max"] == 5.6
        assert data["wind_speed_max"] == 22.4
        assert data["sunrise"] == "07:12"
        assert data["sunset"] == "19:01"
        assert data["humidity_avg"] == round((60 + 70 + 80) / 3)
        assert data["aqi"] == 25
        assert data["pm2_5"] == 5.0
        assert data["pm10"] == 9.0

    def test_fetch_weather_sends_expected_params_and_timeout(self):
        get = MagicMock(side_effect=_fake_get())
        with patch("src.integrations.weather_client.requests.get", get):
            fetch_weather(38.7, -9.1)
        calls = {c.args[0]: c for c in get.call_args_list}
        forecast = calls[weather_client.FORECAST_URL]
        assert forecast.kwargs["params"]["latitude"] == 38.7
        assert forecast.kwargs["params"]["longitude"] == -9.1
        assert forecast.kwargs["params"]["timezone"] == "Europe/Lisbon"
        assert forecast.kwargs["params"]["forecast_days"] == 1
        assert forecast.kwargs["params"]["hourly"] == "relative_humidity_2m"
        assert forecast.kwargs["timeout"] == 15
        aqi = calls[weather_client.AQI_URL]
        assert aqi.kwargs["params"]["hourly"] == "european_aqi,pm2_5,pm10"
        assert aqi.kwargs["timeout"] == 15

    def test_fetch_weather_none_values_fall_back_to_zero(self):
        forecast = _forecast_json(
            precipitation_probability_max=[None],
            precipitation_sum=[None],
            uv_index_max=[None],
            windspeed_10m_max=[None],
        )
        with patch("src.integrations.weather_client.requests.get", side_effect=_fake_get(forecast=forecast)):
            data = fetch_weather(38.7, -9.1)
        assert data["precip_probability"] == 0
        assert data["precip_sum"] == 0.0
        assert data["uv_index_max"] == 0.0
        assert data["wind_speed_max"] == 0.0

    def test_fetch_weather_empty_hourly_lists_fall_back_to_zero(self):
        forecast = _forecast_json()
        forecast["hourly"]["relative_humidity_2m"] = [None, None]
        aqi = _aqi_json(aqi=[None, None], pm25=[], pm10=[None])
        with patch("src.integrations.weather_client.requests.get", side_effect=_fake_get(forecast=forecast, aqi=aqi)):
            data = fetch_weather(38.7, -9.1)
        assert data["humidity_avg"] == 0
        assert data["aqi"] == 0
        assert data["pm2_5"] == 0.0
        assert data["pm10"] == 0.0

    def test_fetch_weather_pm_missing_at_noon_uses_last_valid_value(self):
        pm25 = [1.0, 2.0, 3.5] + [None] * 21  # index 12 is None
        aqi = _aqi_json(pm25=pm25)
        with patch("src.integrations.weather_client.requests.get", side_effect=_fake_get(aqi=aqi)):
            data = fetch_weather(38.7, -9.1)
        assert data["pm2_5"] == 3.5

    def test_fetch_weather_pm_at_noon_preferred_over_others(self):
        pm10 = [1.0] * 12 + [7.5] + [2.0] * 11
        aqi = _aqi_json(pm10=pm10)
        with patch("src.integrations.weather_client.requests.get", side_effect=_fake_get(aqi=aqi)):
            data = fetch_weather(38.7, -9.1)
        assert data["pm10"] == 7.5


class TestFetchWeatherRetry:
    def test_fetch_weather_http_429_raises_without_retry(self):
        get = MagicMock(return_value=_response({}, status=429))
        with patch("src.integrations.weather_client.requests.get", get):
            with pytest.raises(requests.HTTPError):
                fetch_weather(38.7, -9.1)
        assert get.call_count == 1

    def test_fetch_weather_connection_error_retries_then_raises(self):
        get = MagicMock(side_effect=requests.ConnectionError("boom"))
        with patch("src.integrations.weather_client.requests.get", get):
            with pytest.raises(requests.ConnectionError):
                fetch_weather(38.7, -9.1)
        assert get.call_count == 3

    def test_fetch_weather_transient_error_then_success_returns_data(self):
        ok = _fake_get()
        calls = {"n": 0}

        def flaky(url, params=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise requests.Timeout("slow")
            return ok(url, params, timeout)

        with patch("src.integrations.weather_client.requests.get", side_effect=flaky):
            data = fetch_weather(38.7, -9.1)
        assert data["date"] == "2026-03-14"


# ---------------------------------------------------------------------------
# format_weather
# ---------------------------------------------------------------------------

def _sample_data(**overrides):
    data = {
        "date": "2026-03-14",
        "temp_min": 9.4,
        "temp_avg": 14.2,
        "temp_max": 19.6,
        "feels_min": 8.1,
        "feels_max": 18.7,
        "precip_probability": 35,
        "precip_sum": 1.24,
        "uv_index_max": 5.6,
        "wind_speed_max": 22.4,
        "sunrise": "07:12",
        "sunset": "19:01",
        "humidity_avg": 70,
        "aqi": 25,
        "pm2_5": 5.0,
        "pm10": 9.0,
    }
    data.update(overrides)
    return data


class TestFormatWeather:
    def test_format_weather_sample_data_matches_exact_text(self):
        expected = "\n".join([
            "☀️ Weather — Saturday, 14 March 2026",
            "",
            "\U0001f321 Temperature",
            "  Min 9°C · Avg 14°C · Max 20°C",
            "  Feels like: 8°C → 19°C",
            "",
            "\U0001f327 Rain: 35% chance · 1.2mm",
            "\U0001f4a8 Wind: 22 km/h",
            "\U0001f4a7 Humidity: 70%",
            "",
            "\U0001f506 UV Index: 6 (High)",
            "\U0001f305 Sunrise: 07:12 · Sunset: 19:01",
            "",
            "\U0001f32b Air Quality: 25 – Fair",
            "   PM2.5: 5.0 µg/m³ · PM10: 9.0 µg/m³",
        ])
        assert format_weather(_sample_data()) == expected

    @pytest.mark.parametrize("uv,label", [
        (0, "Low"), (2, "Low"), (3, "Moderate"), (5, "Moderate"),
        (6, "High"), (7, "High"), (8, "Very High"), (10, "Very High"), (11, "Extreme"),
    ])
    def test_format_weather_uv_index_maps_to_label(self, uv, label):
        assert f"({label})" in format_weather(_sample_data(uv_index_max=uv))

    @pytest.mark.parametrize("aqi,label", [
        (0, "Good"), (20, "Good"), (21, "Fair"), (40, "Fair"), (41, "Moderate"),
        (60, "Moderate"), (61, "Poor"), (80, "Poor"), (81, "Very Poor"),
        (100, "Very Poor"), (101, "Extremely Poor"),
    ])
    def test_format_weather_aqi_maps_to_label(self, aqi, label):
        assert f"Air Quality: {aqi} – {label}" in format_weather(_sample_data(aqi=aqi))


# ---------------------------------------------------------------------------
# /tempo handler
# ---------------------------------------------------------------------------

def _make_config(lat=38.7, lon=-9.1):
    cfg = MagicMock(spec=Config)
    cfg.telegram_bot_token = "fake-token"
    cfg.telegram_chat_id = "123456"
    cfg.daily_alerts = False
    cfg.weather_latitude = lat
    cfg.weather_longitude = lon
    return cfg


_NEXT_CHAT_ID = 950000


def _make_update():
    global _NEXT_CHAT_ID
    _NEXT_CHAT_ID += 1
    update = MagicMock()
    update.effective_chat.id = _NEXT_CHAT_ID
    update.message = AsyncMock()
    update.message.reply_text = AsyncMock()
    return update


def _make_bot(cfg):
    bot = TelegramBot(cfg, MagicMock())
    bot._auth_check = MagicMock(return_value=True)
    return bot


class TestCmdTempo:
    @pytest.mark.asyncio
    async def test_cmd_tempo_not_configured_replies_not_configured(self):
        bot = _make_bot(_make_config(lat=None, lon=None))
        update = _make_update()
        with patch("src.telegram.commands.system.fetch_weather") as fetch:
            await bot._cmd_tempo(update, MagicMock())
        fetch.assert_not_called()
        text = update.message.reply_text.call_args.args[0]
        assert "não configurad" in text.lower()

    @pytest.mark.asyncio
    async def test_cmd_tempo_only_latitude_set_replies_not_configured(self):
        bot = _make_bot(_make_config(lat=38.7, lon=None))
        update = _make_update()
        with patch("src.telegram.commands.system.fetch_weather") as fetch:
            await bot._cmd_tempo(update, MagicMock())
        fetch.assert_not_called()
        assert "não configurad" in update.message.reply_text.call_args.args[0].lower()

    @pytest.mark.asyncio
    async def test_cmd_tempo_success_replies_with_formatted_weather(self):
        bot = _make_bot(_make_config())
        update = _make_update()
        with patch("src.telegram.commands.system.fetch_weather", return_value=_sample_data()) as fetch:
            await bot._cmd_tempo(update, MagicMock())
        fetch.assert_called_once_with(38.7, -9.1)
        update.message.reply_text.assert_awaited_once_with(format_weather(_sample_data()))

    @pytest.mark.asyncio
    async def test_cmd_tempo_fetch_fails_logs_and_replies_friendly_error(self, caplog):
        bot = _make_bot(_make_config())
        update = _make_update()
        with patch(
            "src.telegram.commands.system.fetch_weather",
            side_effect=requests.ConnectionError("secret-internal-host:443 refused"),
        ):
            with caplog.at_level(logging.ERROR):
                await bot._cmd_tempo(update, MagicMock())
        text = update.message.reply_text.call_args.args[0]
        assert "tempo" in text.lower()
        assert "secret-internal-host" not in text
        assert "Traceback" not in text
        assert any("/tempo" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_cmd_tempo_unauthorized_chat_does_nothing(self):
        bot = _make_bot(_make_config())
        bot._auth_check = MagicMock(return_value=False)
        update = _make_update()
        with patch("src.telegram.commands.system.fetch_weather") as fetch:
            await bot._cmd_tempo(update, MagicMock())
        fetch.assert_not_called()
        update.message.reply_text.assert_not_called()


# ---------------------------------------------------------------------------
# Help text / config
# ---------------------------------------------------------------------------

class TestHelpAndConfig:
    def test_format_help_message_lists_tempo_command(self):
        from src.telegram.formatters import format_help_message
        assert "/tempo" in format_help_message()

    def _load(self, monkeypatch, lat, lon):
        from src import config as config_module
        monkeypatch.setattr(config_module, "load_dotenv", lambda *a, **k: None, raising=False)
        for k, v in {
            "GARMIN_EMAIL": "a@b.c", "GARMIN_PASSWORD": "x",
            "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1",
        }.items():
            monkeypatch.setenv(k, v)
        for name, val in (("WEATHER_LATITUDE", lat), ("WEATHER_LONGITUDE", lon)):
            if val is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, val)
        return config_module.load_config()

    def test_load_config_valid_coordinates_parsed_as_floats(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "d.db"))
        monkeypatch.setenv("LOG_FILE", str(tmp_path / "l.log"))
        cfg = self._load(monkeypatch, "38.7", "-9.1")
        assert cfg.weather_latitude == 38.7
        assert cfg.weather_longitude == -9.1

    def test_load_config_coordinates_absent_are_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "d.db"))
        monkeypatch.setenv("LOG_FILE", str(tmp_path / "l.log"))
        cfg = self._load(monkeypatch, None, None)
        assert cfg.weather_latitude is None
        assert cfg.weather_longitude is None

    def test_load_config_invalid_coordinate_disables_weather_and_warns(self, monkeypatch, tmp_path, caplog):
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "d.db"))
        monkeypatch.setenv("LOG_FILE", str(tmp_path / "l.log"))
        with caplog.at_level(logging.WARNING):
            cfg = self._load(monkeypatch, "abc", "-9.1")
        assert cfg.weather_latitude is None
        assert cfg.weather_longitude is None
        assert any("WEATHER" in r.getMessage() for r in caplog.records)
