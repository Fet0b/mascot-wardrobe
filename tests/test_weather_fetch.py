import http.client
import io
import json
import unittest
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

NOW = datetime(2026, 10, 17, 17, tzinfo=UTC)


def payload(time="2026-10-17T16:45", **overrides):
    data = {
        "current_units": {"temperature_2m": "°C"},
        "current": {"temperature_2m": 22, "time": time},
        "utc_offset_seconds": 0,
    }
    data.update(overrides)
    return data


class FakeOpener:
    """Deterministic transport: records the call and returns or raises a prepared outcome."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def __call__(self, url, timeout=None):
        self.calls.append((url, timeout))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return io.BytesIO(
            self.outcome if isinstance(self.outcome, bytes) else json.dumps(self.outcome).encode()
        )


class BrokenBody:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        raise http.client.IncompleteRead(b"partial")


class FetchTests(unittest.TestCase):
    def fetch(self, outcome, **kwargs):
        from mascot_wardrobe.infrastructure import weather

        opener = FakeOpener(outcome)
        return weather.fetch_weather(-23.5, -46.6, now=NOW, opener=opener, **kwargs), opener

    def test_explicit_coordinates_request_celsius_current_and_parse_real_contract(self):
        reading, opener = self.fetch(payload())
        self.assertEqual(reading.temperature_c, 22)
        url, timeout = opener.calls[0]
        self.assertEqual(timeout, 10)
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["current"], ["temperature_2m"])
        self.assertEqual(query["temperature_unit"], ["celsius"])
        self.assertEqual(query["timezone"], ["GMT"])

    def test_invalid_coordinates_are_rejected_before_any_request(self):
        from mascot_wardrobe.infrastructure import weather

        opener = FakeOpener(payload())
        for latitude, longitude in [(True, 0), (91, 0), (0, float("nan")), (0, 181)]:
            with self.assertRaises(ValueError):
                weather.fetch_weather(latitude, longitude, opener=opener)
        self.assertEqual(opener.calls, [])

    def test_default_transport_is_resolved_at_call_time(self):
        from unittest.mock import patch

        from mascot_wardrobe.infrastructure import weather

        with patch.object(
            weather, "urlopen", return_value=io.BytesIO(json.dumps(payload()).encode())
        ) as fake:
            reading = weather.fetch_weather(-23.5, -46.6, now=NOW)
        self.assertEqual(reading.temperature_c, 22)
        fake.assert_called_once()

    def test_naive_timestamp_uses_utc_offset_seconds(self):
        reading, _ = self.fetch(payload(time="2026-10-17T13:45", utc_offset_seconds=-10800))
        self.assertEqual(reading.observed_at, datetime(2026, 10, 17, 16, 45, tzinfo=UTC))

    def test_stale_future_and_missing_fields_are_weather_errors(self):
        from mascot_wardrobe.infrastructure.weather import WeatherError

        missing_offset = payload()
        del missing_offset["utc_offset_seconds"]
        cases = {
            "stale": payload(time="2026-10-17T13:59"),
            "future": payload(time="2026-10-17T17:16"),
            "no current": {"current_units": {"temperature_2m": "°C"}, "utc_offset_seconds": 0},
            "no offset": missing_offset,
        }
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(WeatherError):
                self.fetch(data)

    def test_transport_and_payload_failures_become_weather_errors_with_cause(self):
        from mascot_wardrobe.infrastructure.weather import WeatherError

        http_500 = HTTPError("https://example.invalid", 500, "Server Error", {}, None)
        self.addCleanup(http_500.close)
        cases = {
            "http 500": http_500,
            "url error": URLError("unreachable"),
            "timeout": TimeoutError("slow"),
            "socket timeout": TimeoutError("slow"),
            "os error": OSError("network down"),
            "incomplete read": http.client.IncompleteRead(b"x"),
            "invalid json": b"{not json",
            "bad utf8": b"\xff\xfe",
            "non-object json": b"[1, 2]",
            "oversize": b" " * 262145,
        }
        for name, outcome in cases.items():
            with self.subTest(name):
                with self.assertRaises(WeatherError) as caught:
                    self.fetch(outcome)
                self.assertTrue(str(caught.exception))
                self.assertTrue(isinstance(caught.exception, ValueError))
                if isinstance(outcome, BaseException):
                    self.assertIs(caught.exception.__cause__, outcome)

    def test_body_read_failure_is_a_weather_error(self):
        from mascot_wardrobe.infrastructure import weather
        from mascot_wardrobe.infrastructure.weather import WeatherError

        with self.assertRaises(WeatherError) as caught:
            weather.fetch_weather(0, 0, now=NOW, opener=lambda url, timeout=None: BrokenBody())
        self.assertIsInstance(caught.exception.__cause__, http.client.IncompleteRead)
