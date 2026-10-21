import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC
from pathlib import Path

from tests.imaging import write_body_png

ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "mascot.png"
        write_body_png(self.source, "#666666")

    def run_cli(self, *args, cwd=ROOT):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        return subprocess.run(
            [sys.executable, "-m", "mascot_wardrobe.interfaces.cli", *map(str, args)],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def generate(self):
        result = self.run_cli(
            "generate",
            "--mascot",
            self.source,
            "--id",
            "cli-pet",
            "--country",
            "BR",
            "--body-box",
            ".2",
            ".5",
            ".8",
            ".9",
            "--output",
            self.root / "catalog",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["manifest"]

    def test_default_output_paths_are_relative_to_the_working_directory(self):
        from mascot_wardrobe.interfaces import cli

        generate = cli.parser().parse_args(
            ["generate", "--mascot", "m.png", "--id", "pet", "--country", "BR"]
        )
        self.assertEqual(generate.output, Path("mascot-wardrobe-out/catalog"))
        demo = cli.parser().parse_args(
            ["demo", "--manifest", "m.json", "--country", "BR", "--temperature", "25"]
        )
        self.assertEqual(demo.state_dir, Path("mascot-wardrobe-out/state"))
        self.assertEqual(demo.export_dir, Path("mascot-wardrobe-out/exports"))
        with tempfile.TemporaryDirectory() as work:
            result = self.run_cli(
                "generate", "--mascot", self.source, "--id", "cli-pet", "--country", "BR", cwd=work
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = Path(json.loads(result.stdout)["manifest"])
            self.assertEqual(
                manifest, Path(work).resolve() / "mascot-wardrobe-out" / "catalog" / "manifest.json"
            )
            self.assertTrue(manifest.is_file())
        self.assertFalse((ROOT / "mascot-wardrobe-out").exists())

    def test_demo_real_http_reports_repeat_noop_without_remote_channel_claims(self):
        manifest = self.generate()
        result = self.run_cli(
            "demo",
            "--manifest",
            manifest,
            "--country",
            "BR",
            "--temperature",
            "25",
            "--state-dir",
            self.root / "state",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["sync"]["action"], "updated")
        self.assertTrue(data["sync"]["verified"])
        self.assertEqual(data["repeat_sync"]["action"], "unchanged")
        self.assertEqual(data["weather"]["source"], "explicit-demo-input-not-live-weather")
        self.assertTrue(data["sandbox"])
        self.assertEqual(
            (self.root / "state" / "current.jpg").read_bytes(), Path(data["selection"]["jpg"]).read_bytes()
        )

    def test_sandbox_other_channels_are_only_local_exports(self):
        manifest = self.generate()
        for channel in ("telegram", "discord", "hermes-cli"):
            result = self.run_cli(
                "sandbox",
                "--manifest",
                manifest,
                "--country",
                "BR",
                "--temperature",
                "25",
                "--channel",
                channel,
                "--export-dir",
                self.root / "exports",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertFalse(data["sync"]["remote_sync"])
            self.assertFalse(data["sync"]["verified"])
            self.assertEqual(data["repeat_sync"]["action"], "unchanged")
            self.assertFalse((self.root / "state").exists())

    def test_live_weather_is_opt_in_and_errors_never_fall_back_to_demo(self):
        import contextlib
        import io
        from datetime import datetime
        from unittest.mock import patch

        from mascot_wardrobe.infrastructure.weather import WeatherReading
        from mascot_wardrobe.interfaces import cli

        manifest = self.generate()
        argv = [
            "select",
            "--manifest",
            manifest,
            "--country",
            "BR",
            "--live-weather",
            "--latitude",
            "-23.5",
            "--longitude",
            "-46.6",
        ]
        stdout = io.StringIO()
        with (
            patch(
                "mascot_wardrobe.infrastructure.weather.fetch_weather",
                return_value=WeatherReading(22, datetime(2026, 10, 17, 17, tzinfo=UTC)),
            ) as fetch,
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(cli.main(argv), 0)
        fetch.assert_called_once_with(-23.5, -46.6)
        self.assertEqual(json.loads(stdout.getvalue())["weather"]["source"], "open-meteo")
        with patch("mascot_wardrobe.infrastructure.weather.fetch_weather", side_effect=OSError("offline")):
            with (
                contextlib.redirect_stderr(io.StringIO()),
                contextlib.redirect_stdout(io.StringIO()) as failed,
            ):
                self.assertEqual(cli.main(argv), 2)
            self.assertEqual(failed.getvalue(), "")

    def test_live_weather_transport_failure_exits_2_with_error_line(self):
        import contextlib
        import io
        from unittest.mock import patch
        from urllib.error import URLError

        from mascot_wardrobe.interfaces import cli

        manifest = self.generate()
        argv = [
            "select",
            "--manifest",
            str(manifest),
            "--country",
            "BR",
            "--live-weather",
            "--latitude",
            "-23.5",
            "--longitude",
            "-46.6",
        ]
        out, err = io.StringIO(), io.StringIO()
        with (
            patch("mascot_wardrobe.infrastructure.weather.urlopen", side_effect=URLError("unreachable")),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            self.assertEqual(cli.main(argv), 2)
        self.assertEqual(out.getvalue(), "")
        self.assertTrue(err.getvalue().startswith("error: Weather service unavailable"))

    def test_generate_select_cli_accepts_png_identity_body_box_and_fahrenheit(self):
        manifest = self.generate()
        result = self.run_cli(
            "select", "--manifest", manifest, "--country", "BR", "--temperature", "77", "--unit", "F"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        selected = json.loads(result.stdout)
        self.assertEqual(selected["temperature_c"], 25)
        self.assertEqual(selected["category"], "warm")
        self.assertEqual(selected["country_code"], "BR")
