"""Weather for the configured location from Open-Meteo (free, no API key).

https://open-meteo.com/en/docs
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from dashboard.schema import Item

URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 15

# WMO weather codes -> short description.
WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "rain showers", 81: "rain showers", 82: "heavy rain showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail",
}


def request_params(config: dict) -> dict:
    loc = config["location"]
    return {
        "latitude": loc["latitude"],
        "longitude": loc["longitude"],
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": config.get("timezone", "America/Chicago"),
        "forecast_days": 1,
    }


def what_to_wear(feels_like: float, rain_chance: int, wind_mph: float, code: int) -> str:
    """Rule-based one-liner for the walk to campus. The LLM may refine this in M5."""
    if feels_like < 20:
        layer = "Heavy coat, hat and gloves"
    elif feels_like < 40:
        layer = "Winter coat"
    elif feels_like < 55:
        layer = "Jacket"
    elif feels_like < 68:
        layer = "Light jacket or sweater"
    else:
        layer = "T-shirt weather"
    extras = []
    if code in (71, 73, 75, 77, 85, 86):
        extras.append("boots for the snow")
    elif rain_chance >= 40:
        extras.append("bring an umbrella")
    if wind_mph >= 20:
        extras.append("windy off the lake")
    return layer + (", " + ", ".join(extras) if extras else "") + "."


def parse(payload: dict, config: dict) -> list[Item]:
    cur, day = payload["current"], payload["daily"]
    temp = round(cur["temperature_2m"])
    feels = round(cur["apparent_temperature"])
    code = int(cur["weather_code"])
    high = round(day["temperature_2m_max"][0])
    low = round(day["temperature_2m_min"][0])
    rain = int(day["precipitation_probability_max"][0] or 0)
    wind = float(cur["wind_speed_10m"])

    feels_note = f", feels like {feels}°F" if abs(feels - temp) >= 3 else ""
    return [
        Item(
            source="weather",
            title=f"{temp}°F, {WMO.get(code, 'unknown conditions')}{feels_note}",
            summary=f"High {high} / low {low}, {rain}% chance of rain. {what_to_wear(feels, rain, wind, code)}",
            link="https://open-meteo.com/",
            timestamp=cur["time"],
        )
    ]


def fetch(config: dict) -> list[Item]:
    url = f"{URL}?{urllib.parse.urlencode(request_params(config))}"
    with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
        payload = json.load(resp)
    return parse(payload, config)
