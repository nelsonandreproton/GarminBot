"""Open-Meteo weather client for the /tempo command (forecast + air quality)."""

from __future__ import annotations

import logging
from datetime import date as date_type
from typing import TypedDict

import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AQI_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
_TIMEOUT_SECONDS = 15
_TIMEZONE = "Europe/Lisbon"


class WeatherData(TypedDict):
    date: str
    temp_min: float
    temp_avg: float
    temp_max: float
    feels_min: float
    feels_max: float
    precip_probability: int
    precip_sum: float
    uv_index_max: float
    wind_speed_max: float
    sunrise: str
    sunset: str
    humidity_avg: int
    aqi: int
    pm2_5: float
    pm10: float


def _is_retryable(exc: BaseException) -> bool:
    """Retry connection errors, timeouts and 5xx — never 4xx (incl. 429 rate limit)."""
    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        return status is not None and status >= 500
    return isinstance(exc, requests.RequestException)


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def _get_json(url: str, params: dict) -> dict:
    resp = requests.get(url, params=params, timeout=_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def _first_valid(values: list, preferred_index: int = 12) -> float:
    if len(values) > preferred_index and values[preferred_index] is not None:
        return float(values[preferred_index])
    valid = [v for v in values if v is not None]
    return float(valid[-1]) if valid else 0.0


def fetch_weather(latitude: float, longitude: float) -> WeatherData:
    """Fetch today's forecast and air quality for the given coordinates."""
    forecast_params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": (
            "temperature_2m_min,temperature_2m_mean,temperature_2m_max,"
            "apparent_temperature_min,apparent_temperature_max,"
            "precipitation_sum,precipitation_probability_max,"
            "uv_index_max,windspeed_10m_max,sunrise,sunset"
        ),
        "hourly": "relative_humidity_2m",
        "timezone": _TIMEZONE,
        "forecast_days": 1,
    }
    aqi_params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "european_aqi,pm2_5,pm10",
        "timezone": _TIMEZONE,
        "forecast_days": 1,
    }

    forecast_json = _get_json(FORECAST_URL, forecast_params)
    aqi_hourly = _get_json(AQI_URL, aqi_params)["hourly"]
    daily = forecast_json["daily"]
    hourly = forecast_json["hourly"]

    humidity_values = [v for v in hourly["relative_humidity_2m"] if v is not None]
    humidity_avg = round(sum(humidity_values) / len(humidity_values)) if humidity_values else 0

    aqi_values = [v for v in aqi_hourly["european_aqi"] if v is not None]
    aqi = int(max(aqi_values)) if aqi_values else 0

    return WeatherData(
        date=daily["time"][0],
        temp_min=daily["temperature_2m_min"][0],
        temp_avg=daily["temperature_2m_mean"][0],
        temp_max=daily["temperature_2m_max"][0],
        feels_min=daily["apparent_temperature_min"][0],
        feels_max=daily["apparent_temperature_max"][0],
        precip_probability=daily["precipitation_probability_max"][0] or 0,
        precip_sum=daily["precipitation_sum"][0] or 0.0,
        uv_index_max=daily["uv_index_max"][0] or 0.0,
        wind_speed_max=daily["windspeed_10m_max"][0] or 0.0,
        sunrise=daily["sunrise"][0].split("T")[1],
        sunset=daily["sunset"][0].split("T")[1],
        humidity_avg=humidity_avg,
        aqi=aqi,
        pm2_5=_first_valid(aqi_hourly["pm2_5"]),
        pm10=_first_valid(aqi_hourly["pm10"]),
    )


def _uv_label(uv: float) -> str:
    if uv <= 2:
        return "Low"
    if uv <= 5:
        return "Moderate"
    if uv <= 7:
        return "High"
    if uv <= 10:
        return "Very High"
    return "Extreme"


def _aqi_label(aqi: int) -> str:
    if aqi <= 20:
        return "Good"
    if aqi <= 40:
        return "Fair"
    if aqi <= 60:
        return "Moderate"
    if aqi <= 80:
        return "Poor"
    if aqi <= 100:
        return "Very Poor"
    return "Extremely Poor"


def format_weather(data: WeatherData) -> str:
    """Format weather data as the plain-text /tempo message."""
    d = date_type.fromisoformat(data["date"])
    date_str = f"{d.day} {d.strftime('%B %Y')}"
    day_str = d.strftime("%A")

    lines = [
        f"☀️ Weather — {day_str}, {date_str}",
        "",
        "\U0001f321 Temperature",
        f"  Min {round(data['temp_min'])}°C · Avg {round(data['temp_avg'])}°C · Max {round(data['temp_max'])}°C",
        f"  Feels like: {round(data['feels_min'])}°C → {round(data['feels_max'])}°C",
        "",
        f"\U0001f327 Rain: {data['precip_probability']}% chance · {data['precip_sum']:.1f}mm",
        f"\U0001f4a8 Wind: {round(data['wind_speed_max'])} km/h",
        f"\U0001f4a7 Humidity: {data['humidity_avg']}%",
        "",
        f"\U0001f506 UV Index: {data['uv_index_max']:.0f} ({_uv_label(data['uv_index_max'])})",
        f"\U0001f305 Sunrise: {data['sunrise']} · Sunset: {data['sunset']}",
        "",
        f"\U0001f32b Air Quality: {data['aqi']} – {_aqi_label(data['aqi'])}",
        f"   PM2.5: {data['pm2_5']:.1f} µg/m³ · PM10: {data['pm10']:.1f} µg/m³",
    ]
    return "\n".join(lines)
