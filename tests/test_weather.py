import unittest
from datetime import UTC, datetime


class WeatherTests(unittest.TestCase):
    def test_model_reading_normalizes_timestamp_and_rejects_stale_units_and_numbers(self):
        try:
            from mascot_wardrobe.infrastructure.weather import parse_weather
        except ImportError as exc:
            self.fail(f"Weather adapter not implemented: {exc}")
        now = datetime(2026, 10, 17, 17, 0, tzinfo=UTC)

        def payload(value=22, unit="°C", time="2026-10-17T16:45"):
            return {
                "current_units": {"temperature_2m": unit},
                "current": {"temperature_2m": value, "time": time},
                "utc_offset_seconds": 0,
            }

        r = parse_weather(payload(), now)
        self.assertEqual(r.temperature_c, 22)
        self.assertEqual(r.observed_at.tzinfo, UTC)
        for data in [
            payload(True),
            payload(float("nan")),
            payload(unit="°F"),
            payload(time="2026-10-16T00:00"),
            payload(time="2026-10-17T18:00"),
        ]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_weather(data, now)
