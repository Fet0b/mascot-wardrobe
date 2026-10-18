"""Optional Open-Meteo readings; failures never become invented temperatures."""

import http.client
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import urlopen

from mascot_wardrobe.domain.models import validate_temperature

URL = "https://api.open-meteo.com/v1/forecast"
MAX_BYTES = 262144


class WeatherError(ValueError):
    """A live weather reading could not be obtained or trusted."""


@dataclass(frozen=True)
class WeatherReading:
    temperature_c: float
    observed_at: datetime
    source: str = "open-meteo"


def fetch_weather(latitude, longitude, *, now=None, timeout=10, opener=None):
    for value, limit in ((latitude, 90), (longitude, 180)):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or abs(value) > limit
        ):
            raise ValueError("Explicit finite latitude/longitude required")
    query = urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m",
            "temperature_unit": "celsius",
            "timezone": "GMT",
        }
    )
    open_url = urlopen if opener is None else opener
    try:
        with open_url(URL + "?" + query, timeout=timeout) as response:
            raw = response.read(MAX_BYTES + 1)
    except OSError as error:  # HTTPError, URLError, TimeoutError and other network failures
        raise WeatherError(
            f"Weather service unavailable ({type(error).__name__}); no temperature was assumed"
        ) from error
    except http.client.HTTPException as error:
        raise WeatherError(
            "Weather service returned an incomplete response; no temperature was assumed"
        ) from error
    if len(raw) > MAX_BYTES:
        raise WeatherError("Weather response too large")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WeatherError("Weather response is not valid JSON") from error
    if not isinstance(payload, dict):
        raise WeatherError("Weather response must be a JSON object")
    return parse_weather(payload, now)


def parse_weather(payload, now=None):
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Reference time must be timezone-aware")
    try:
        if payload["current_units"]["temperature_2m"] != "°C":
            raise WeatherError("Weather must explicitly use Celsius")
        temperature = validate_temperature(payload["current"]["temperature_2m"])
        observed = datetime.fromisoformat(payload["current"]["time"])
        if observed.tzinfo is None:
            offset = payload["utc_offset_seconds"]
            if isinstance(offset, bool) or not isinstance(offset, int) or abs(offset) > 86400:
                raise WeatherError("Invalid UTC offset")
            observed = observed.replace(tzinfo=timezone(timedelta(seconds=offset)))
        observed = observed.astimezone(UTC)
        age = now.astimezone(UTC) - observed
        if age > timedelta(hours=3) or age < -timedelta(minutes=15):
            raise WeatherError("Weather reading is stale or in the future")
        return WeatherReading(temperature, observed)
    except WeatherError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise WeatherError("Malformed weather reading") from error
