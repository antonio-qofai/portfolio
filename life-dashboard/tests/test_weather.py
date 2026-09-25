from connectors.weather import parse, request_params, what_to_wear
from dashboard.config import load_config

# Trimmed real Open-Meteo response for Chicago, 2026-09-25.
PAYLOAD = {
    "current": {
        "time": "2026-09-25T14:45",
        "temperature_2m": 60.8,
        "apparent_temperature": 58.9,
        "weather_code": 3,
        "wind_speed_10m": 7.5,
    },
    "daily": {
        "time": ["2026-09-25"],
        "temperature_2m_max": [62.8],
        "temperature_2m_min": [57.5],
        "precipitation_probability_max": [12],
        "weather_code": [3],
    },
}


def test_parse_open_meteo():
    [item] = parse(PAYLOAD, load_config())
    assert item.source == "weather"
    assert item.title == "61°F, overcast"
    assert item.summary.startswith("High 63 / low 58, 12% chance of rain.")
    assert item.section == "personal"


def test_feels_like_shown_only_when_different():
    payload = {**PAYLOAD, "current": {**PAYLOAD["current"], "apparent_temperature": 52.0}}
    [item] = parse(payload, load_config())
    assert "feels like 52°F" in item.title


def test_request_uses_config_location():
    params = request_params(load_config())
    assert params["latitude"] == 41.8781 and params["temperature_unit"] == "fahrenheit"


def test_what_to_wear():
    assert what_to_wear(10, 0, 5, 0).startswith("Heavy coat")
    assert "umbrella" in what_to_wear(60, 60, 5, 61)
    assert "boots" in what_to_wear(28, 80, 5, 73)
    assert "windy" in what_to_wear(45, 0, 25, 2)
    assert what_to_wear(75, 10, 5, 0) == "T-shirt weather."
