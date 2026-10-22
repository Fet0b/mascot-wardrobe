"""Weather-aware publishing of a saved closet look: dry-run default, fail-closed, WhatsApp only."""

import contextlib
import hashlib
import io
import json
import re
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from PIL import Image

from mascot_wardrobe.application import closet_weather
from mascot_wardrobe.closet import Closet
from mascot_wardrobe.infrastructure.policies import load_profile
from mascot_wardrobe.interfaces import cli
from mascot_wardrobe.wardrobe import CATEGORIES, profile_photo
from tests.test_cli_publish import HEADER, SlugBridge
from tests.test_closet import write_mascot, write_mask


class ClosetFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        write_mascot(self.base / "m.png")
        write_mask(self.base / "mask.png")
        self.root = self.base / "closet"
        self.closet = Closet.init(self.root, self.base / "m.png", self.base / "mask.png")
        self.ids = {}
        for category in CATEGORIES:
            self.ids[category] = "look-" + category.replace("_", "-")
            self.closet.add_item(self.ids[category], category)
        self.closet.save_wardrobe("looks", list(self.ids.values()))
        self.profile = load_profile("BR")


class SelectionTests(ClosetFixture):
    def test_every_category_boundary_picks_exactly_the_profile_category(self):
        bounds = self.profile.thresholds_c
        probes = [(bounds[0] - 0.1, 0)]
        for index, bound in enumerate(bounds):
            probes.append((bound, index + 1))
        for temperature, index in probes:
            with self.subTest(temperature=temperature):
                look = closet_weather.select_closet_look(self.root, "looks", "BR", temperature)
                self.assertEqual(look["category"], CATEGORIES[index])
                self.assertEqual(look["item_id"], self.ids[CATEGORIES[index]])

    def test_missing_category_fails_closed_for_every_temperature(self):
        self.closet.save_wardrobe("looks", [i for c, i in self.ids.items() if c != "warm"])
        for temperature in (28.0, 10.0):
            with (
                self.subTest(temperature=temperature),
                self.assertRaisesRegex(ValueError, "exactly six.*warm"),
            ):
                closet_weather.select_closet_look(self.root, "looks", "BR", temperature)

    def test_duplicate_category_fails_closed_even_for_other_temperatures(self):
        self.closet.add_item("second-cold", "cold")
        self.closet.save_wardrobe("looks", [*self.ids.values(), "second-cold"])
        with self.assertRaisesRegex(ValueError, "duplicate: cold"):
            closet_weather.select_closet_look(self.root, "looks", "BR", 30.0)

    def test_selection_reports_slug_and_exact_asset_without_touching_the_closet(self):
        before = (self.root / "closet.json").read_bytes()
        look = closet_weather.select_closet_look(self.root, "looks", "BR", 22.0)
        self.assertEqual(look["slug"], "closet-looks-" + look["item_id"])
        self.assertEqual(Path(look["asset"]), self.closet.item_path(look["item_id"]))
        self.assertEqual(look["asset_sha256"], self.closet.items()[look["item_id"]]["sha256"])
        self.assertEqual((self.root / "closet.json").read_bytes(), before)
        self.assertIsNone(Closet.open(self.root).active)

    def test_unsafe_stale_and_invalid_inputs_fail_closed(self):
        for name in ("nope", "../looks", "Looks", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                closet_weather.select_closet_look(self.root, name, "BR", 22.0)
        for value in (float("nan"), float("inf"), True, "20", None, -91, 66):
            with self.subTest(value=value), self.assertRaises(ValueError):
                closet_weather.select_closet_look(self.root, "looks", "BR", value)
        self.closet.edit_item(self.ids["mild"], style="streetwear")
        with self.assertRaisesRegex(ValueError, "stale"):
            closet_weather.select_closet_look(self.root, "looks", "BR", 22.0)


NOW = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)
BACKGROUND = (0x10, 0x2B, 0x32)


def close(pixel, expected, tolerance=6):
    return max(abs(a - b) for a, b in zip(pixel, expected, strict=True)) <= tolerance


class StateFileTests(ClosetFixture):
    def write_state(self, **fields):
        path = self.base / "state.json"
        payload = {
            "temperature_c": 22.0,
            "updated_at_utc": NOW.isoformat(),
            "current_time_utc": NOW.isoformat(),
            "slug": "ignored-by-the-reader",
            **fields,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def read(self, path, **kwargs):
        return closet_weather.read_weather_state(path, now=NOW, **kwargs)

    def test_updater_state_returns_celsius_and_update_time(self):
        stamp = NOW - timedelta(minutes=50)
        reading = self.read(self.write_state(temperature_c=17.5, updated_at_utc=stamp.isoformat()))
        self.assertEqual(reading.temperature_c, 17.5)
        self.assertEqual(reading.observed_at, stamp)

    def test_current_time_utc_is_only_a_fallback(self):
        path = self.base / "state.json"
        stamp = (NOW - timedelta(minutes=5)).isoformat()
        path.write_text(json.dumps({"temperature_c": 20, "current_time_utc": stamp}), encoding="utf-8")
        self.assertEqual(self.read(path).observed_at.isoformat(), stamp)
        old = (NOW - timedelta(hours=9)).isoformat()
        path = self.write_state(updated_at_utc=old, current_time_utc=NOW.isoformat())
        with self.assertRaisesRegex(ValueError, "stale"):
            self.read(path)

    def test_stale_future_naive_and_malformed_timestamps_are_refused(self):
        for stamp in (
            (NOW - timedelta(hours=3, minutes=1)).isoformat(),
            (NOW + timedelta(minutes=16)).isoformat(),
            "2026-10-22T14:30:00",
            "yesterday",
            "",
            None,
            True,
            1.5,
        ):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                self.read(self.write_state(updated_at_utc=stamp))

    def test_max_age_is_configurable(self):
        path = self.write_state(updated_at_utc=(NOW - timedelta(minutes=90)).isoformat())
        with self.assertRaisesRegex(ValueError, "stale"):
            self.read(path, max_age=timedelta(hours=1))
        self.assertEqual(self.read(path, max_age=timedelta(hours=2)).temperature_c, 22.0)

    def test_bad_temperatures_and_malformed_documents_are_refused(self):
        for value in (None, "22", True, float("nan"), 1e9, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.read(self.write_state(temperature_c=value))
        path = self.base / "state.json"
        stamp = NOW.isoformat()
        for text in ("", "{", "[]", "null", json.dumps({"updated_at_utc": stamp}), '{"temperature_c": 20}'):
            path.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.read(path)

    def test_missing_oversized_directory_and_symlink_state_are_refused(self):
        with self.assertRaises(ValueError):
            self.read(self.base / "absent.json")
        with self.assertRaises(ValueError):
            self.read(self.base)
        big = self.base / "big.json"
        big.write_text(" " * 70_000 + "{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.read(big)
        link = self.base / "link.json"
        link.symlink_to(self.write_state())
        with self.assertRaises(ValueError):
            self.read(link)

    def test_reading_state_writes_nothing(self):
        path = self.write_state()
        before = sorted((p.name, p.stat().st_mtime_ns) for p in self.base.rglob("*"))
        self.read(path)
        self.assertEqual(sorted((p.name, p.stat().st_mtime_ns) for p in self.base.rglob("*")), before)


class ComposeTests(ClosetFixture):
    def look_png(self):
        return Path(closet_weather.select_closet_look(self.root, "looks", "BR", 22.0)["asset"]).read_bytes()

    def decode(self, data):
        with Image.open(io.BytesIO(data)) as image:
            return image.format, image.size, image.convert("RGB")

    def test_output_is_a_1024_jpeg_on_the_approved_background(self):
        data = profile_photo.compose_profile_jpeg(self.look_png())
        form, size, rgb = self.decode(data)
        self.assertEqual((form, size), ("JPEG", (1024, 1024)))
        for corner in ((0, 0), (1023, 0), (0, 1023), (1023, 1023)):
            self.assertTrue(close(rgb.getpixel(corner), BACKGROUND))
        self.assertFalse(close(rgb.getpixel((512, 512)), BACKGROUND, 20))

    def test_visible_pixels_stay_inside_the_circular_safe_area_and_use_most_of_it(self):
        _, _, rgb = self.decode(profile_photo.compose_profile_jpeg(self.look_png()))
        limit = 512 * profile_photo.SAFE_RADIUS_FRACTION
        self.assertLessEqual(profile_photo.SAFE_RADIUS_FRACTION, 0.9)
        radii = [
            ((x - 511.5) ** 2 + (y - 511.5) ** 2) ** 0.5
            for y in range(0, 1024, 2)
            for x in range(0, 1024, 2)
            if not close(rgb.getpixel((x, y)), BACKGROUND, 24)
        ]
        self.assertLessEqual(max(radii), limit + 4)
        self.assertGreaterEqual(max(radii), limit * 0.8)  # scaled to fill the safe area, not tiny

    def test_background_must_be_an_explicit_hex_colour(self):
        for value in ("102b32", "#12", "#gggggg", "red", None, 0x102B32):
            with self.subTest(value=value), self.assertRaises(ValueError):
                profile_photo.compose_profile_jpeg(self.look_png(), background=value)
        _, _, rgb = self.decode(profile_photo.compose_profile_jpeg(self.look_png(), background="#FF0000"))
        self.assertTrue(close(rgb.getpixel((0, 0)), (255, 0, 0)))

    def test_invalid_item_images_are_refused(self):
        empty = io.BytesIO()
        Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(empty, "PNG")
        jpeg = io.BytesIO()
        Image.new("RGB", (64, 64)).save(jpeg, "JPEG")
        for data in (b"", b"not an image", empty.getvalue(), jpeg.getvalue()):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                profile_photo.compose_profile_jpeg(data)

    def test_optional_overlay_is_external_generic_and_validated(self):
        plain = profile_photo.compose_profile_jpeg(self.look_png())
        overlay = self.base / "overlay.png"
        marked = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
        marked.paste((255, 0, 0, 255), (480, 480, 544, 544))
        marked.save(overlay)
        with_overlay = profile_photo.compose_profile_jpeg(self.look_png(), overlay=overlay)
        self.assertNotEqual(plain, with_overlay)
        red, _green, blue = self.decode(with_overlay)[2].getpixel((512, 512))
        self.assertGreater(red, 200)
        self.assertLess(blue, 60)
        (self.base / "bad.png").write_bytes(b"nope")
        wide = self.base / "wide.png"
        Image.new("RGBA", (64, 32)).save(wide)
        link = self.base / "link.png"
        link.symlink_to(overlay)
        for bad in (self.base / "bad.png", self.base / "missing.png", self.base, wide, link):
            with self.subTest(bad=bad.name), self.assertRaises(ValueError):
                profile_photo.compose_profile_jpeg(self.look_png(), overlay=bad)

    def test_overlay_is_clipped_to_the_circular_safe_area(self):
        overlay = self.base / "full.png"
        Image.new("RGBA", (1024, 1024), (255, 0, 0, 255)).save(overlay)
        _, _, rgb = self.decode(profile_photo.compose_profile_jpeg(self.look_png(), overlay=overlay))
        limit = 512 * profile_photo.SAFE_RADIUS_FRACTION
        self.assertTrue(close(rgb.getpixel((512, 512)), (255, 0, 0), 12))
        for y in range(0, 1024, 8):
            for x in range(0, 1024, 8):
                distance = ((x - 511.5) ** 2 + (y - 511.5) ** 2) ** 0.5
                if distance > limit + 12:  # JPEG ringing reaches a few pixels past a hard edge
                    self.assertTrue(close(rgb.getpixel((x, y)), BACKGROUND), (x, y))

    def test_composition_is_deterministic_and_bridge_sized(self):
        first = profile_photo.compose_profile_jpeg(self.look_png())
        self.assertEqual(first, profile_photo.compose_profile_jpeg(self.look_png()))
        self.assertLess(len(first), 2 * 1024 * 1024)


ROOT = Path(__file__).resolve().parents[1]


class DocsContractTests(unittest.TestCase):
    def read(self, name):
        return (ROOT / name).read_text(encoding="utf-8")

    def test_example_is_uninstalled_dry_run_first_and_state_file_driven(self):
        folder = ROOT / "examples" / "closet-weather"
        names = sorted(p.name for p in folder.iterdir())
        self.assertEqual(names, ["mascot-wardrobe-closet-weather.service.example"])
        text = (folder / names[0]).read_text(encoding="utf-8")
        for needle in (
            "Never installed by mascot-wardrobe",
            "mascot-wardrobe publish-closet",
            "--state-file",
            "--wardrobe",
            "--apply",
            "EnvironmentFile=",
            "--whatsapp-bridge-url http://127.0.0.1:",
        ):
            self.assertIn(needle, text)
        self.assertNotIn("--live-weather", text)
        self.assertNotIn("--latitude", text)
        self.assertNotIn("telegram", text.lower())
        self.assertIsNone(re.search(r"\d{6,}:[A-Za-z0-9_-]{20,}", text))
        self.assertIsNone(re.search(r"^\s*(sudo|systemctl)\b", text, flags=re.MULTILINE))

    def test_docs_cover_usage_rollback_and_that_the_cli_does_not_activate_the_timer(self):
        doc = self.read("docs/closet.md")
        for needle in (
            "publish-closet",
            "--state-file",
            "dry-run",
            "--apply",
            "Rollback",
            "does not activate",
            "updated_at_utc",
            "#102b32",
        ):
            self.assertIn(needle, doc)
        self.assertNotIn("No per-category (weather) mapping", doc)
        self.assertIn("publish-closet", self.read("docs/scheduler.md"))
        self.assertIn("publish-closet", self.read("CHANGELOG.md"))
        self.assertIn("examples/closet-weather/", self.read("tests/test_sdist.py"))


def tree_state(root):
    """Every path under ``root`` with size, mtime and content hash: any write changes it."""
    state = {}
    for path in sorted(Path(root).rglob("*")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        state[str(path)] = (path.stat().st_size, path.stat().st_mtime_ns, digest)
    return state


class CliTests(ClosetFixture):
    def setUp(self):
        super().setUp()
        self.approved = self.base / "approved"
        self.approved.mkdir()
        self.whatsapp_state = self.base / "whatsapp-state"

    def write_state(self, **fields):
        path = self.base / "state.json"
        stamp = datetime.now(UTC).isoformat()
        payload = {"temperature_c": 22.0, "updated_at_utc": stamp, "current_time_utc": stamp, **fields}
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(
                ["publish-closet", "--closet-root", str(self.root), "--wardrobe", "looks", "--country", "BR"]
                + [str(a) for a in args]
            )
        return code, out.getvalue(), err.getvalue()

    def whatsapp_args(self, url="http://127.0.0.1:1"):
        return (
            "--whatsapp-catalog-dir",
            self.approved,
            "--whatsapp-state-dir",
            self.whatsapp_state,
            "--whatsapp-bridge-url",
            url,
            "--whatsapp-bridge-header",
            HEADER,
        )

    def test_dry_run_from_state_file_reports_the_plan_with_zero_writes_and_requests(self):
        state = self.write_state(temperature_c=22.0)
        before = tree_state(self.base)
        with (
            mock.patch("mascot_wardrobe.channels.live.HermesWhatsAppProfileAdapter._request") as request,
            mock.patch("socket.socket.connect") as connect,
        ):
            code, out, _ = self.run_cli("--state-file", state, *self.whatsapp_args())
        result = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(result["mode"], "dry-run")
        self.assertEqual(result["category"], "ambient")
        self.assertEqual(result["item_id"], self.ids["ambient"])
        self.assertEqual(result["slug"], "closet-looks-" + self.ids["ambient"])
        self.assertEqual(result["weather"]["source"], "state-file")
        self.assertEqual(result["channels"], {"whatsapp": {"status": "planned"}})
        self.assertIn("would", result["action"])
        self.assertEqual((result["width"], result["height"], result["background"]), (1024, 1024, "#102b32"))
        self.assertEqual(len(result["jpg_sha256"]), 64)
        request.assert_not_called()
        connect.assert_not_called()
        self.assertEqual(tree_state(self.base), before)
        self.assertEqual(list(self.approved.iterdir()), [])
        self.assertIsNone(Closet.open(self.root).active)

    def test_apply_stages_the_selected_jpeg_verifies_and_repeats_unchanged(self):
        state = self.write_state(temperature_c=22.0)
        closet_before = tree_state(self.root)
        with SlugBridge(self.approved) as bridge:
            args = ("--state-file", state, "--apply", *self.whatsapp_args(bridge.url))
            code, out, _ = self.run_cli(*args)
            first = json.loads(out)
            code2, out2, _ = self.run_cli(*args)
        slug = "closet-looks-" + self.ids["ambient"]
        self.assertEqual((code, code2), (0, 0))
        self.assertEqual(first["mode"], "apply")
        self.assertEqual(first["channels"]["whatsapp"]["action"], "updated")
        self.assertTrue(first["channels"]["whatsapp"]["verified"])
        self.assertEqual(json.loads(out2)["channels"]["whatsapp"]["action"], "unchanged")
        self.assertEqual(bridge.posts, [slug])
        self.assertEqual(sorted(p.name for p in self.approved.iterdir()), [slug + ".jpg"])
        self.assertEqual(
            sorted(p.name for p in self.whatsapp_state.iterdir()),
            ["avatar-sync.json", "avatar-sync.lock", "whatsapp-profile-before-sync.jpg"],
        )
        self.assertTrue(all(p.is_file() for p in self.approved.iterdir()))
        staged = (self.approved / (slug + ".jpg")).read_bytes()
        self.assertEqual(hashlib.sha256(staged).hexdigest(), first["jpg_sha256"])
        with Image.open(io.BytesIO(staged)) as image:
            self.assertEqual((image.format, image.size), ("JPEG", (1024, 1024)))
        self.assertEqual(tree_state(self.root), closet_before)
        self.assertIsNone(Closet.open(self.root).active)
        self.assertNotIn("telegram", first["channels"])

    def test_stale_state_exits_2_without_writes_or_requests(self):
        old = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        state = self.write_state(updated_at_utc=old, current_time_utc=old)
        before = tree_state(self.base)
        with mock.patch("socket.socket.connect") as connect:
            code, out, err = self.run_cli("--state-file", state, "--apply", *self.whatsapp_args())
        self.assertEqual((code, out), (2, ""))
        self.assertIn("stale", err)
        connect.assert_not_called()
        self.assertEqual(tree_state(self.base), before)

    def test_temperature_sources_are_exclusive_celsius_for_state_and_manual_path_works(self):
        state = self.write_state()
        for args in ((), ("--temperature", 20, "--state-file", state)):  # argparse exits with status 2
            with self.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(io.StringIO()):
                self.run_cli(*args)
            self.assertEqual(raised.exception.code, 2)
        code, _, err = self.run_cli("--state-file", state, "--unit", "F")
        self.assertEqual(code, 2)
        self.assertIn("Celsius", err)
        code, out, _ = self.run_cli("--temperature", 50, "--unit", "F")
        self.assertEqual(code, 0)
        self.assertEqual((json.loads(out)["temperature_c"], json.loads(out)["category"]), (10.0, "cold"))
        self.assertEqual(json.loads(out)["weather"]["source"], "explicit-input-not-live-weather")

    def test_apply_needs_complete_whatsapp_settings_and_telegram_does_not_exist(self):
        before = tree_state(self.base)
        self.assertEqual(self.run_cli("--temperature", 20, "--apply")[0], 2)
        code, _, err = self.run_cli("--temperature", 20, "--whatsapp-catalog-dir", self.approved)
        self.assertEqual(code, 2)
        self.assertIn("whatsapp", err)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_cli("--temperature", 20, "--telegram")
        self.assertEqual(tree_state(self.base), before)

    def test_unsafe_names_and_backgrounds_exit_2(self):
        for extra in (("--background", "red"), ("--background", "102b32")):
            self.assertEqual(self.run_cli("--temperature", 20, *extra)[0], 2, extra)
        for name in ("../looks", "a/b", "UPPER", "under_score"):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(
                    ["publish-closet", "--closet-root", str(self.root), "--wardrobe", name]
                    + ["--country", "BR", "--temperature", "20"]
                )
            self.assertEqual(code, 2, name)

    def test_bridge_failure_exits_1_and_leaves_the_closet_alone(self):
        before = tree_state(self.root)
        code, out, _ = self.run_cli("--temperature", 20, "--apply", *self.whatsapp_args("http://127.0.0.1:1"))
        self.assertEqual(code, 1)
        result = json.loads(out)
        self.assertEqual(result["channels"]["whatsapp"]["status"], "failed")
        self.assertFalse(result["ok"])
        self.assertEqual(tree_state(self.root), before)
        self.assertFalse((self.whatsapp_state / "whatsapp-profile-before-sync.jpg").exists())
        self.assertFalse((self.whatsapp_state / "avatar-sync.json").exists())

    def test_overlay_is_applied_only_when_supplied_and_reported(self):
        overlay = self.base / "overlay.png"
        Image.new("RGBA", (1024, 1024), (255, 0, 0, 255)).save(overlay)
        plain = json.loads(self.run_cli("--temperature", 20)[1])
        marked = json.loads(self.run_cli("--temperature", 20, "--overlay", overlay)[1])
        self.assertNotEqual(plain["jpg_sha256"], marked["jpg_sha256"])
        self.assertEqual((plain["overlay"], marked["overlay"]), (False, True))
        self.assertEqual(self.run_cli("--temperature", 20, "--overlay", self.base / "missing.png")[0], 2)

    def test_underscore_item_ids_are_rejected_before_any_write(self):
        path = self.root / "closet.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        old = self.ids["ambient"]
        data["items"]["look_ambient"] = data["items"].pop(old)
        for entry in data["wardrobes"]["looks"]["items"]:
            if entry["id"] == old:
                entry["id"] = "look_ambient"
        path.write_text(json.dumps(data), encoding="utf-8")
        before = tree_state(self.base)
        code, out, err = self.run_cli(
            "--temperature", 22, "--apply", *self.whatsapp_args("http://127.0.0.1:1")
        )
        self.assertEqual((code, out), (2, ""))
        self.assertIn("error:", err)
        self.assertEqual(tree_state(self.base), before)

    def test_longest_closet_names_still_make_a_bridge_safe_slug(self):
        from mascot_wardrobe.channels import _slug

        wardrobe = "w" * 40
        self.closet.add_item("i" * 40, "ambient")
        self.closet.save_wardrobe(wardrobe, [*(v for k, v in self.ids.items() if k != "ambient"), "i" * 40])
        look = closet_weather.select_closet_look(self.root, wardrobe, "BR", 22.0)
        self.assertEqual(_slug(look["slug"]), look["slug"])

    def test_a_slug_that_is_not_bridge_safe_is_refused_before_any_write(self):
        bad = {**closet_weather.select_closet_look(self.root, "looks", "BR", 22.0), "slug": "Bad_Slug"}
        before = tree_state(self.base)
        with (
            mock.patch.object(closet_weather, "select_closet_look", return_value=bad),
            self.assertRaisesRegex(ValueError, "slug"),
        ):
            closet_weather.publish_closet_look(self.root, "looks", "BR", 22.0)
        self.assertEqual(tree_state(self.base), before)
