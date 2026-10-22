"""Portable legacy four-band selection, separate from the six-category country policy.

No locations, account slugs, fetching, scheduler or runtime mutation lives here.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mascot_wardrobe.domain.models import validate_temperature
from mascot_wardrobe.infrastructure.weather import WeatherReading, parse_weather


@dataclass(frozen=True)
class LegacySelection:
    band: str
    reading: WeatherReading


def legacy_band(temperature):
    temperature = validate_temperature(temperature)
    if not -30 <= temperature <= 60:
        raise ValueError("Legacy temperature must be within -30..60 Celsius")
    return (
        "cold" if temperature < 15 else "cool" if temperature < 22 else "mild" if temperature < 28 else "hot"
    )


def parse_legacy_weather(payload, *, now=None):
    """Celsius, -30..60, age 0..3h inclusive; explicit offset or IANA zone for naive times."""
    now = now or datetime.now(UTC)
    try:
        current = dict(payload["current"])
        observed = datetime.fromisoformat(current["time"])
        if observed.tzinfo is None and payload.get("utc_offset_seconds") is None:
            current["time"] = observed.replace(tzinfo=ZoneInfo(payload["timezone"])).isoformat()
        reading = parse_weather({**payload, "current": current}, now)
        if reading.observed_at > now:
            raise ValueError("Legacy weather timestamp is in the future")
        return LegacySelection(legacy_band(reading.temperature_c), reading)
    except (KeyError, TypeError, ZoneInfoNotFoundError):
        raise ValueError("Invalid legacy weather response") from None
