from __future__ import annotations

from typing import Any

import httpx
from django.utils import timezone

from core.models import WeatherSource

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


WEATHER_LABELS = {
    0: "Ясно",
    1: "Преимущественно ясно",
    2: "Переменная облачность",
    3: "Облачно",
    45: "Туман",
    48: "Изморозь",
    51: "Слабая морось",
    53: "Морось",
    55: "Сильная морось",
    61: "Слабый дождь",
    63: "Дождь",
    65: "Сильный дождь",
    71: "Слабый снег",
    73: "Снег",
    75: "Сильный снег",
    77: "Снежные зёрна",
    80: "Небольшие ливни",
    81: "Ливни",
    82: "Сильные ливни",
    85: "Слабый снегопад",
    86: "Сильный снегопад",
    95: "Гроза",
    96: "Гроза с градом",
    99: "Сильная гроза с градом",
}


def fetch_open_meteo(source: WeatherSource) -> dict[str, Any]:
    params = {
        "latitude": str(source.latitude),
        "longitude": str(source.longitude),
        "timezone": source.timezone_name,
        "current": ",".join(
            [
                "temperature_2m",
                "apparent_temperature",
                "relative_humidity_2m",
                "weather_code",
                "wind_speed_10m",
                "wind_direction_10m",
                "is_day",
            ]
        ),
        "daily": ",".join(
            [
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_probability_max",
            ]
        ),
        "forecast_days": 4,
    }
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        response = client.get(OPEN_METEO_URL, params=params)
        response.raise_for_status()
        payload = response.json()

    current = payload.get("current", {})
    code = int(current.get("weather_code", -1))
    return {
        "provider": "open_meteo",
        "location": source.name,
        "observedAt": current.get("time"),
        "temperature": current.get("temperature_2m"),
        "apparentTemperature": current.get("apparent_temperature"),
        "humidity": current.get("relative_humidity_2m"),
        "weatherCode": code,
        "condition": WEATHER_LABELS.get(code, "Нет данных"),
        "windSpeed": current.get("wind_speed_10m"),
        "windDirection": current.get("wind_direction_10m"),
        "isDay": bool(current.get("is_day", 1)),
        "daily": payload.get("daily", {}),
        "units": payload.get("current_units", {}),
        "fetchedAt": timezone.now().isoformat(),
    }


def update_weather(source: WeatherSource) -> dict[str, Any]:
    source.last_attempt_at = timezone.now()
    try:
        if source.provider != "open_meteo":
            raise ValueError(f"Неизвестный провайдер погоды: {source.provider}")
        data = fetch_open_meteo(source)
    except Exception as exc:
        source.last_error = str(exc)[:2000]
        source.save(update_fields=["last_attempt_at", "last_error", "updated_at"])
        raise

    source.current_data = data
    source.last_success_at = timezone.now()
    source.last_error = ""
    source.save(
        update_fields=[
            "current_data",
            "last_attempt_at",
            "last_success_at",
            "last_error",
            "updated_at",
        ]
    )
    return data
