"""The legacy four-band policy is independent of country profiles and saved closets."""

from datetime import UTC, datetime, timedelta

import pytest

from mascot_wardrobe.infrastructure import weather

NOW = datetime(2026, 10, 22, 15, tzinfo=UTC)


def payload(temperature=22, **fields):
    return {
        "current_units": {"temperature_2m": "°C"},
        "current": {"temperature_2m": temperature, "time": "2026-10-22T12:00:00"},
        "timezone": "America/Sao_Paulo",
        **fields,
    }


@pytest.mark.parametrize(
    "temperature,band",
    [
        (-30, "cold"),
        (14.99, "cold"),
        (15, "cool"),
        (21.99, "cool"),
        (22, "mild"),
        (27.99, "mild"),
        (28, "hot"),
        (60, "hot"),
    ],
)
def test_exact_legacy_boundaries(temperature, band):
    from mascot_wardrobe.infrastructure.legacy_weather import legacy_band, parse_legacy_weather

    assert legacy_band(temperature) == band
    result = parse_legacy_weather(payload(temperature), now=NOW)
    assert result.band == band
    assert result.reading.observed_at == NOW
    assert result.reading.temperature_c == temperature


@pytest.mark.parametrize(
    "temperature", [-30.01, 60.01, True, "22", None, float("inf"), float("nan"), 10**1000]
)
def test_legacy_rejects_invalid_temperatures(temperature):
    from mascot_wardrobe.infrastructure.legacy_weather import parse_legacy_weather

    with pytest.raises(ValueError):
        parse_legacy_weather(payload(temperature), now=NOW)


@pytest.mark.parametrize(
    "age,valid",
    [(timedelta(hours=3), True), (timedelta(hours=3, microseconds=1), False), (timedelta(seconds=-1), False)],
)
def test_legacy_freshness_is_stricter_than_publisher_future_tolerance(age, valid):
    from mascot_wardrobe.infrastructure.legacy_weather import parse_legacy_weather

    data = payload()
    data["current"]["time"] = (NOW - age).isoformat()
    if valid:
        assert parse_legacy_weather(data, now=NOW).band == "mild"
    else:
        with pytest.raises(ValueError):
            parse_legacy_weather(data, now=NOW)


def test_explicit_offset_wins_and_fahrenheit_and_unknown_zones_fail():
    from mascot_wardrobe.infrastructure.legacy_weather import parse_legacy_weather

    assert parse_legacy_weather(payload(utc_offset_seconds=-10800), now=NOW).reading.observed_at == NOW
    data = payload(timezone="Invalid/Zone")
    with pytest.raises(ValueError):
        parse_legacy_weather(data, now=NOW)
    data = payload()
    data["current_units"]["temperature_2m"] = "°F"
    with pytest.raises(ValueError):
        parse_legacy_weather(data, now=NOW)


def test_new_profile_and_legacy_bands_remain_distinct():
    from mascot_wardrobe.infrastructure.legacy_weather import legacy_band
    from mascot_wardrobe.infrastructure.policies import load_profile

    assert legacy_band(22) == "mild"
    assert load_profile("BR").classify(22).value == "ambient"
    # Neither parser invents a location; fetching still requires coordinates and an injected opener.
    with pytest.raises(ValueError, match="latitude/longitude"):
        weather.fetch_weather(None, None, opener=lambda *a, **k: pytest.fail("network forbidden"))
