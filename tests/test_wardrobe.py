"""Real tests for the generation context."""

import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BOX = (0.22, 0.60, 0.78, 0.88)
EXAMPLE_BODY_BOX = (0.125, 0.555, 0.875, 0.915)  # same value as art/generate_art.py
SYNTHETIC_BOX = (0.0, 0.28, 1.0, 0.92)  # overlaps the head of the synthetic mascot


def head_over_body_image(centre=120, nubs=True):
    """Synthetic mascot: a head ellipse overlapping a body ellipse (narrow neck), optional arm nubs."""
    image = Image.new("RGBA", (240, 250), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    fill = (130, 180, 210, 255)
    draw.ellipse((centre - 65, 100, centre + 65, 230), fill=fill)
    draw.ellipse((centre - 45, 10, centre + 45, 110), fill=fill)
    if nubs:
        draw.ellipse((centre - 78, 135, centre - 52, 175), fill=fill)
        draw.ellipse((centre + 52, 135, centre + 78, 175), fill=fill)
    return image


def crop_to_box(image, box):
    width, height = image.size
    pixels = tuple(int(v * n) for v, n in zip(box, (width, height, width, height), strict=True))
    return image.crop(pixels)


def source_image():
    """A body ellipse that fills the default box except at its bottom, with two eyes above the box."""
    image = Image.new("RGBA", (160, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((10, 70, 150, 185), fill=(130, 180, 210, 255))
    draw.ellipse((60, 90, 70, 100), fill="black")
    draw.ellipse((92, 90, 102, 100), fill="black")
    return image


class ComposerTests(unittest.TestCase):
    def test_six_distinct_outfits_preserve_face_and_transparency(self):
        from mascot_wardrobe.wardrobe import LocalComposer, OutfitBrief

        source = source_image()
        categories = ("very_cold", "cold", "mild", "ambient", "warm", "very_hot")
        hashes = set()
        for category in categories:
            brief = OutfitBrief(
                category, ("#2878b5", "#e8b84b"), ("casaco",), (0.22, 0.60, 0.78, 0.88), "Local instruction"
            )
            result = LocalComposer().render(source, brief)
            self.assertEqual(result.mode, "RGBA")
            self.assertEqual(result.size, source.size)
            self.assertEqual(result.crop((0, 0, 160, 120)).tobytes(), source.crop((0, 0, 160, 120)).tobytes())
            self.assertEqual(result.getpixel((0, 0)), (0, 0, 0, 0))
            hashes.add(hashlib.sha256(result.tobytes()).hexdigest())
        self.assertEqual(len(hashes), 6)
        self.assertEqual(source.tobytes(), source_image().tobytes())

    def test_composer_rejects_invalid_category_box_palette_and_source(self):
        from mascot_wardrobe.wardrobe import LocalComposer, OutfitBrief

        cases = [
            dict(category="../../outside"),
            dict(body_box=(0.2, 0.6, 1.2, 0.9)),
            dict(body_box=(0.8, 0.6, 0.2, 0.9)),
            dict(body_box=(0.2, float("nan"), 0.8, 0.9)),
            dict(palette=()),
            dict(palette=("not-a-color",)),
        ]
        for change in cases:
            values = dict(
                category="cold",
                palette=("#123456",),
                garments=("casaco",),
                body_box=(0.22, 0.6, 0.78, 0.88),
                text="local",
            )
            values.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                LocalComposer().render(source_image(), OutfitBrief(**values))
        with self.assertRaises(ValueError):
            LocalComposer().render(
                Image.new("RGB", (160, 200)),
                OutfitBrief("cold", ("#123456",), (), (0.22, 0.6, 0.78, 0.88), "local"),
            )

    def test_output_paths_reject_traversal_symlinks_and_unsafe_names(self):
        from mascot_wardrobe.wardrobe import _safe_root, _safe_target

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "out"
            root.mkdir()
            external = base / "outside"
            external.mkdir()
            (root / "pingo.png").symlink_to(external / "victim.png")
            (base / "alias").symlink_to(external, target_is_directory=True)
            for name in (
                "../victim.png",
                "/tmp/asset.png",
                "nested/asset.png",
                "pingo.png",
                "evil\\\\asset.png",
            ):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    _safe_target(root, name)
            for path in (base / "alias", base / "alias" / "child", root / ".." / "other"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    _safe_root(path)
            self.assertEqual(_safe_target(root, "pingo-br-cold.png"), root / "pingo-br-cold.png")
            self.assertEqual(list(external.iterdir()), [])


class AgentTests(unittest.TestCase):
    def fixture(self, root, **mascot_kwargs):
        from mascot_wardrobe.domain.models import CountryProfile, Mascot

        path = root / "input.png"
        source_image().save(path)
        return (
            Mascot("pingo", path, **mascot_kwargs),
            CountryProfile(
                "BR",
                (0, 10, 18, 25, 32),
                ("#2878b5", "#e8b84b", "#663399", "#337744", "#cc8844", "#55aacc"),
                ("local coat", "jacket", "sweater", "t-shirt", "tank top", "light piece"),
            ),
        )

    def test_generate_real_six_asset_sets_and_honest_manifest(self):
        from mascot_wardrobe.wardrobe import WardrobeAgent

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mascot, profile = self.fixture(root)
            original = mascot.image_path.read_bytes()
            manifest_path = WardrobeAgent().generate(mascot, profile, root / "out")
            self.assertEqual(manifest_path, root / "out" / "manifest.json")
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["generator"], "local-compositor")
            self.assertEqual(manifest["mascot_id"], "pingo")
            self.assertEqual(manifest["country_code"], "BR")
            self.assertIn("determin", manifest["provenance"]["method"])
            self.assertFalse(manifest["provenance"]["remote_inference"])
            self.assertEqual(
                manifest["atlas_layout"]["state_rows"],
                [
                    "idle",
                    "running-right",
                    "running-left",
                    "waving",
                    "jumping",
                    "failed",
                    "waiting",
                    "running",
                    "review",
                ],
            )
            self.assertEqual(manifest["atlas_layout"]["usable_frames_per_state"], 6)
            self.assertEqual(len(manifest["outfits"]), 6)
            hashes = set()
            for outfit in manifest["outfits"]:
                self.assertEqual(outfit["slug"], "pingo-br-" + outfit["category"].replace("_", "-"))
                self.assertIn("BR", outfit["brief"])
                self.assertIn("local coat", outfit["brief"])
                for key in ("png", "jpg", "atlas"):
                    relative = Path(outfit[key])
                    self.assertFalse(relative.is_absolute())
                    self.assertNotIn("..", relative.parts)
                    with Image.open(manifest_path.parent / relative) as image:
                        image.load()
                        if key == "png":
                            self.assertEqual(
                                (image.format, image.mode, image.size), ("PNG", "RGBA", (160, 200))
                            )
                            self.assertEqual(
                                image.crop((0, 0, 160, 120)).tobytes(),
                                source_image().crop((0, 0, 160, 120)).tobytes(),
                            )
                            hashes.add(hashlib.sha256(image.tobytes()).hexdigest())
                        elif key == "jpg":
                            self.assertEqual(
                                (image.format, image.mode, image.size), ("JPEG", "RGB", (512, 512))
                            )
                        else:
                            self.assertEqual(
                                (image.format, image.mode, image.size), ("PNG", "RGBA", (1536, 1872))
                            )
                            for row in range(9):
                                frames = [
                                    image.crop((col * 192, row * 208, (col + 1) * 192, (row + 1) * 208))
                                    for col in range(6)
                                ]
                                self.assertTrue(all(frame.getbbox() for frame in frames))
                                self.assertEqual(
                                    len({hashlib.sha256(frame.tobytes()).hexdigest() for frame in frames}), 6
                                )
                            self.assertIsNone(image.crop((1152, 0, 1536, 1872)).getbbox())
            self.assertEqual(len(hashes), 6)
            self.assertEqual(mascot.image_path.read_bytes(), original)

    def test_agent_rejects_unsafe_requests_before_writing(self):
        from types import SimpleNamespace

        from mascot_wardrobe.wardrobe import WardrobeAgent

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mascot, profile = self.fixture(root)

            def pet(**changes):
                values = dict(
                    mascot_id=mascot.mascot_id, image_path=mascot.image_path, body_box=mascot.body_box
                )
                values.update(changes)
                return SimpleNamespace(**values)

            def country(**changes):
                values = dict(
                    country_code=profile.country_code,
                    thresholds_c=profile.thresholds_c,
                    palette=profile.palette,
                    garments=profile.garments,
                )
                values.update(changes)
                return SimpleNamespace(**values)

            cases = [
                (pet(mascot_id="../escaped"), profile),
                (pet(mascot_id="Uppercase"), profile),
                (mascot, country(country_code="../US")),
                (mascot, country(country_code="br")),
                (mascot, country(thresholds_c=(0, 1))),
                (mascot, country(thresholds_c=(0, 10, 8, 25, 32))),
                (mascot, country(palette=())),
                (mascot, country(garments=())),
                (pet(body_box=(0.1, 0.6, 1.2, 0.9)), profile),
            ]
            for index, (request, configuration) in enumerate(cases):
                output = root / f"out-{index}"
                with self.subTest(index=index), self.assertRaises(ValueError):
                    WardrobeAgent().generate(request, configuration, output)
                self.assertFalse(output.exists(), "Invalid request must not write artifacts")
            invalid = root / "bad.png"
            invalid.write_bytes(b"not an image")
            with self.assertRaisesRegex(ValueError, "PNG"):
                WardrobeAgent().generate(pet(image_path=invalid), profile, root / "bad")
            jpeg = root / "hidden.png"
            source_image().convert("RGB").save(jpeg, format="JPEG")
            with self.assertRaisesRegex(ValueError, "PNG"):
                WardrobeAgent().generate(pet(image_path=jpeg), profile, root / "bad")
            with self.assertRaises(ValueError):
                WardrobeAgent().generate(
                    pet(image_path=Path("https://example.org/a.png")), profile, root / "remote"
                )
            self.assertFalse((root / "escaped-br-very-cold.png").exists())

    def test_injected_renderer_missing_category_fails_without_manifest_or_assets(self):
        from mascot_wardrobe.wardrobe import LocalComposer, WardrobeAgent

        class MissingLast:
            generator = "local-compositor"

            def render(self, source, brief):
                if brief.category == "very_hot":
                    return None
                return LocalComposer().render(source, brief)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mascot, profile = self.fixture(root)
            output = root / "out"
            with self.assertRaisesRegex(ValueError, "very_hot"):
                WardrobeAgent(MissingLast()).generate(mascot, profile, output)
            self.assertFalse(output.exists())

    def test_generation_rejects_symlink_asset_and_does_not_touch_external_file(self):
        from mascot_wardrobe.wardrobe import WardrobeAgent

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mascot, profile = self.fixture(root)
            output = root / "out"
            output.mkdir()
            victim = root / "victim"
            victim.write_bytes(b"unchanged")
            (output / "pingo-br-very-cold.png").symlink_to(victim)
            with self.assertRaises(ValueError):
                WardrobeAgent().generate(mascot, profile, output)
            self.assertEqual(victim.read_bytes(), b"unchanged")
            self.assertFalse((output / "manifest.json").exists())

    def test_generate_once_reuses_verified_assets_and_rejects_changed_request(self):
        from dataclasses import replace

        from mascot_wardrobe.wardrobe import LocalComposer, WardrobeAgent

        class CountingComposer(LocalComposer):
            def __init__(self):
                self.calls = []

            def render(self, source, brief):
                self.calls.append(brief.category)
                return super().render(source, brief)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mascot, profile = self.fixture(root)
            renderer = CountingComposer()
            agent = WardrobeAgent(renderer)
            output = root / "out"
            manifest = agent.generate(mascot, profile, output)
            original = manifest.read_bytes()
            self.assertEqual(agent.generate(mascot, profile, output), manifest)
            self.assertEqual(len(renderer.calls), 6)
            self.assertEqual(manifest.read_bytes(), original)
            with self.assertRaises(ValueError):
                agent.generate(mascot, replace(profile, country_code="US"), output)
            png = output / json.loads(original)["outfits"][0]["png"]
            png.write_bytes(b"corrupted")
            with self.assertRaises(ValueError):
                agent.generate(mascot, profile, output)
            self.assertEqual(len(renderer.calls), 6)

    def test_reuse_binds_input_policy_body_box_and_explicit_renderer_config(self):
        from dataclasses import replace

        from mascot_wardrobe.wardrobe import LocalComposer, WardrobeAgent

        class ConfiguredComposer(LocalComposer):
            cache_config = {"style": "local", "revision": 1}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mascot, profile = self.fixture(root)
            renderer = ConfiguredComposer()
            agent = WardrobeAgent(renderer)
            output = root / "out"
            target = agent.generate(mascot, profile, output)
            original = target.read_bytes()
            changes = [
                (replace(mascot, mascot_id="other"), profile),
                (replace(mascot, body_box=(0.25, 0.6, 0.75, 0.88)), profile),
                (mascot, replace(profile, thresholds_c=(1, 10, 18, 25, 32))),
                (mascot, replace(profile, palette=("#123456",) + profile.palette[1:])),
                (mascot, replace(profile, garments=("different",) + profile.garments[1:])),
            ]
            for changed_mascot, changed_profile in changes:
                with (
                    self.subTest(mascot=changed_mascot, profile=changed_profile),
                    self.assertRaises(ValueError),
                ):
                    agent.generate(changed_mascot, changed_profile, output)
            image_bytes = mascot.image_path.read_bytes()
            mascot.image_path.write_bytes(image_bytes + b"changed PNG bytes")
            with self.assertRaises(ValueError):
                agent.generate(mascot, profile, output)
            mascot.image_path.write_bytes(image_bytes)
            with self.assertRaises(ValueError):
                WardrobeAgent(LocalComposer()).generate(mascot, profile, output)
            renderer.cache_config = {"style": "local", "revision": 2}
            with self.assertRaises(ValueError):
                agent.generate(mascot, profile, output)
            renderer.cache_config = {"style": "local", "revision": 1}
            self.assertEqual(WardrobeAgent(ConfiguredComposer()).generate(mascot, profile, output), target)
            self.assertEqual(target.read_bytes(), original)

    def test_default_renderer_binds_a_template_revision_so_stale_catalogs_are_rejected(self):
        from unittest.mock import patch

        from mascot_wardrobe.wardrobe import LocalComposer, WardrobeAgent

        config = LocalComposer.cache_config
        self.assertEqual(json.loads(json.dumps(config)), config)
        self.assertGreaterEqual(config["templates"], 2)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mascot, profile = self.fixture(root)
            output = root / "out"
            # Keep the concrete renderer class unchanged: only the template configuration differs.
            with patch.object(LocalComposer, "cache_config", None):
                legacy = WardrobeAgent(LocalComposer()).generate(mascot, profile, output)
            self.assertIsNone(json.loads(legacy.read_text())["request"]["renderer_config"])
            before = legacy.read_bytes()
            with self.assertRaises(ValueError):
                WardrobeAgent(LocalComposer()).generate(mascot, profile, output)
            self.assertEqual(legacy.read_bytes(), before)
            fresh = WardrobeAgent(LocalComposer()).generate(mascot, profile, root / "fresh")
            self.assertEqual(
                json.loads(fresh.read_text())["request"]["renderer_config"], LocalComposer.cache_config
            )

    def test_reuse_rejects_incomplete_malformed_or_unsafe_cache_without_rendering(self):
        from copy import deepcopy

        from mascot_wardrobe.wardrobe import LocalComposer, WardrobeAgent

        class CountingComposer(LocalComposer):
            def __init__(self):
                self.calls = 0

            def render(self, source, brief):
                self.calls += 1
                return super().render(source, brief)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mascot, profile = self.fixture(root)
            renderer = CountingComposer()
            agent = WardrobeAgent(renderer)
            output = root / "out"
            target = agent.generate(mascot, profile, output)
            original = target.read_bytes()
            pristine = json.loads(original)
            cases = []
            for field, value in (
                ("schema_version", 999),
                ("outfits", [pristine["outfits"][0]] * 6),
                ("generator", "fake-remote"),
                ("provenance", {"remote_inference": True}),
                ("atlas_layout", {"rows": 1}),
            ):
                changed = deepcopy(pristine)
                changed[field] = value
                cases.append(json.dumps(changed))
            for key in ("png", "jpg", "atlas"):
                for name in (
                    "../input.png",
                    str(mascot.image_path),
                    "nested/asset.png",
                    "missing.png",
                    "manifest.json",
                ):
                    changed = deepcopy(pristine)
                    changed["outfits"][0][key] = name
                    cases.append(json.dumps(changed))
            cases.extend(("null", "[]", '"bad"', "{", "{}"))
            for index, content in enumerate(cases):
                target.write_text(content)
                with self.subTest(index=index), self.assertRaises(ValueError):
                    agent.generate(mascot, profile, output)
                self.assertEqual(target.read_text(), content)
            target.write_bytes(original)
            for outfit in pristine["outfits"]:
                for key in ("png", "jpg", "atlas"):
                    asset = output / outfit[key]
                    content = asset.read_bytes()
                    asset.unlink()
                    with self.subTest(asset=asset, kind="missing"), self.assertRaises(ValueError):
                        agent.generate(mascot, profile, output)
                    asset.symlink_to(mascot.image_path)
                    with self.subTest(asset=asset, kind="symlink"), self.assertRaises(ValueError):
                        agent.generate(mascot, profile, output)
                    asset.unlink()
                    asset.write_bytes(content)
            target.unlink()
            external = root / "external.json"
            external.write_bytes(original)
            target.symlink_to(external)
            with self.assertRaises(ValueError):
                agent.generate(mascot, profile, output)
            self.assertEqual(external.read_bytes(), original)
            self.assertEqual(renderer.calls, 6)


class MeasureFitTests(unittest.TestCase):
    def measure(self, image, box, region=None):
        from mascot_wardrobe.wardrobe.fit import measure_fit

        alpha = crop_to_box(image, box).getchannel("A")
        return measure_fit(alpha, None if region is None else crop_to_box(region, box).getchannel("A"))

    def example(self):
        return Image.open(ROOT / "examples" / "mascot.png").convert("RGBA")

    def example_mask(self):
        example = self.example()
        mask = Image.new("RGBA", example.size, (0, 0, 0, 0))
        mask.putalpha(example.getchannel("A"))
        return mask

    def test_head_over_body_fixture_starts_the_garment_at_the_neck(self):
        fit = self.measure(head_over_body_image(), SYNTHETIC_BOX)
        self.assertEqual(fit.top_row, 0)
        self.assertTrue(30 <= fit.neck_row <= 36, fit.neck_row)  # narrowest row, box row 33
        self.assertTrue(94 <= fit.neck_left <= 100 and 140 <= fit.neck_right <= 146)
        self.assertTrue(56 <= fit.shoulder_row <= 62, fit.shoulder_row)
        self.assertTrue(157 <= fit.hem_row <= 160, fit.hem_row)
        self.assertEqual(fit.height, len(fit.extents))
        self.assertEqual(fit.height, len(fit.torso))

    def test_arm_nubs_are_outside_the_torso(self):
        with_nubs = self.measure(head_over_body_image(), SYNTHETIC_BOX)
        without = self.measure(head_over_body_image(nubs=False), SYNTHETIC_BOX)
        row = 75  # box row through the nubs (image y 145)
        left, right = with_nubs.extents[row]
        t_left, t_right = with_nubs.torso[row]
        self.assertTrue(left < t_left - 5 and right > t_right + 5)
        # the torso matches the body measured without nubs (the belly window is a median estimate)
        self.assertLessEqual(abs(t_left - without.extents[row][0]), 3)
        self.assertLessEqual(abs(t_right - without.extents[row][1]), 3)

    def test_off_centre_body_follows_the_silhouette_not_the_box(self):
        centred = self.measure(head_over_body_image(120), SYNTHETIC_BOX)
        shifted = self.measure(head_over_body_image(150), SYNTHETIC_BOX)
        self.assertEqual(shifted.neck_row, centred.neck_row)
        self.assertEqual(shifted.shoulder_row, centred.shoulder_row)
        self.assertEqual(shifted.neck_left - centred.neck_left, 30)
        self.assertEqual(shifted.neck_right - centred.neck_right, 30)
        self.assertEqual(shifted.torso[100][0] - centred.torso[100][0], 30)

    def test_box_starting_below_the_chin_starts_at_the_top_row(self):
        fit = self.measure(head_over_body_image(), (0.0, 0.5, 1.0, 0.92))
        self.assertEqual(fit.neck_row, fit.top_row)

    def test_real_mascot_with_the_default_box(self):
        fit = self.measure(self.example(), DEFAULT_BOX)  # the head is fully above the box
        self.assertEqual(fit.height, 143)
        self.assertEqual((fit.top_row, fit.neck_row), (0, 0))
        self.assertTrue(8 <= fit.neck_left <= 10 and 276 <= fit.neck_right <= 280)
        self.assertLessEqual(fit.shoulder_row, 4)
        self.assertEqual(fit.hem_row, 142)

    def test_real_mascot_with_the_example_body_box(self):
        fit = self.measure(self.example(), EXAMPLE_BODY_BOX)
        self.assertEqual(fit.height, 184)
        self.assertEqual((fit.top_row, fit.neck_row), (0, 0))
        self.assertTrue(89 <= fit.neck_left <= 93 and 290 <= fit.neck_right <= 294)
        self.assertTrue(20 <= fit.shoulder_row <= 30, fit.shoulder_row)  # arm nubs start at row 24
        self.assertEqual(fit.hem_row, 183)
        left, right = fit.extents[100]  # through the arm nubs
        t_left, t_right = fit.torso[100]
        self.assertTrue(left <= 12 and right >= 372)
        self.assertTrue(38 <= t_left <= 42 and 340 <= t_right <= 346, (t_left, t_right))

    def test_closet_mask_built_from_the_example_silhouette(self):
        example, mask = self.example(), self.example_mask()
        for box in (DEFAULT_BOX, EXAMPLE_BODY_BOX):
            with self.subTest(box=box):
                self.assertEqual(self.measure(example, box, mask), self.measure(example, box))

    def test_mask_over_an_opaque_image_succeeds(self):
        opaque = Image.new("RGBA", self.example().size, (200, 200, 200, 255))
        fit = self.measure(opaque, EXAMPLE_BODY_BOX, self.example_mask())
        self.assertEqual(fit, self.measure(self.example(), EXAMPLE_BODY_BOX))

    def test_fit_error_when_no_trustworthy_silhouette_exists(self):
        from mascot_wardrobe.wardrobe.fit import FitError, measure_fit

        empty = Image.new("L", (100, 100), 0)
        tiny = Image.new("L", (8, 8), 0)
        ImageDraw.Draw(tiny).rectangle((2, 2, 5, 5), fill=255)
        opaque = Image.new("L", (100, 100), 255)
        wide_head = Image.new("L", (100, 100), 0)  # wide band over a narrow body: no sound neck
        ImageDraw.Draw(wide_head).rectangle((0, 0, 99, 30), fill=255)
        ImageDraw.Draw(wide_head).rectangle((40, 31, 60, 99), fill=255)
        cases = (("empty", empty), ("tiny", tiny), ("opaque", opaque), ("inconsistent", wide_head))
        for name, image in cases:
            with self.subTest(name=name), self.assertRaises(FitError):
                measure_fit(image)
        self.assertTrue(issubclass(FitError, ValueError))
        with self.assertRaises(FitError):
            measure_fit(opaque, Image.new("L", (50, 50), 255))  # mask size differs
        with self.assertRaises(FitError):
            measure_fit(opaque, empty)  # the mask leaves no silhouette

    def test_a_full_width_odd_belly_keeps_its_whole_width(self):
        fit = self.measure(source_image(), (0.22, 0.60, 0.78, 0.88))  # 89 columns, narrower at the hem
        self.assertEqual(fit.torso[20], (0, 88))
        self.assertEqual((fit.neck_row, fit.shoulder_row), (0, 0))

    def test_measurement_is_deterministic(self):
        first = self.measure(self.example(), EXAMPLE_BODY_BOX)
        second = self.measure(self.example(), EXAMPLE_BODY_BOX)
        self.assertEqual(first, second)


PROBE = ("#ff00aa", "#00e5ff")  # colours that never occur in the mascots, so a changed pixel is garment
CATEGORY_NAMES = ("very_cold", "cold", "mild", "ambient", "warm", "very_hot")


def pixel_box(box, size):
    width, height = size
    return tuple(int(v * n) for v, n in zip(box, (width, height, width, height), strict=True))


def garment_mask(source, result):
    """255 wherever the result differs from the source in any channel."""
    r, g, b, a = ImageChops.difference(source, result).split()
    changed = ImageChops.lighter(ImageChops.lighter(r, g), ImageChops.lighter(b, a))
    return changed.point(lambda v: 255 if v > 0 else 0)


def row_span(mask, y):
    data = mask.tobytes()
    row = data[y * mask.width : (y + 1) * mask.width]
    left = row.find(255)
    return None if left < 0 else (left, row.rfind(255))


def rect_region(image, box_px):
    """An edit mask like the closet's: the silhouette inside the rectangle, protected elsewhere."""
    mask = Image.new("L", image.size, 0)
    keep = image.getchannel("A").point(lambda v: 255 if v > 127 else 0).crop(box_px)
    mask.paste(keep, box_px[:2])
    return mask


def head_mask(size):
    """Head of examples/mascot.png (art/generate_art.py), shrunk by 2 px."""
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse((256 - 180, 171 - 132, 256 + 180, 171 + 132), fill=255)
    return mask


class FitAssertions:
    """Symptom-specific fit checks: neckline, shoulders, sides, sleeves, hem, protected pixels."""

    def assert_fit(self, source, result, box_px, region, index, *, head=False, style="classic"):
        from mascot_wardrobe.wardrobe import composer
        from mascot_wardrobe.wardrobe.fit import measure_fit

        x0, y0, x1, y1 = box_px
        w, h = x1 - x0, y1 - y0
        alpha = source.getchannel("A").crop(box_px)
        fit = measure_fit(alpha, None if region is None else region.crop(box_px))
        inset = composer.side_inset(style, w)
        dip = composer.dip_rows(index, h)
        span = composer.sleeve_span(index, fit, style)
        gm = garment_mask(source, result).crop(box_px)
        silhouette = alpha.point(lambda v: 255 if v > 127 else 0)
        if region is not None:
            kept = region.crop(box_px).point(lambda v: 255 if v > 127 else 0)
            silhouette = ImageChops.multiply(silhouette, kept)
        label = f"category {index}"

        # protected pixels: nothing outside the box, nothing above the neck, no head pixel
        expected = source.copy()
        expected.paste(result.crop(box_px), (x0, y0))
        self.assertEqual(expected.tobytes(), result.tobytes(), f"{label}: pixels outside the box changed")
        self.assertEqual(
            result.crop((x0, y0, x1, y0 + fit.neck_row)).tobytes(),
            source.crop((x0, y0, x1, y0 + fit.neck_row)).tobytes(),
            f"{label}: pixels above the neck row changed",
        )
        if head:
            painted = ImageChops.multiply(garment_mask(source, result), head_mask(source.size))
            self.assertIsNone(painted.getbbox(), f"{label}: head pixels were painted")
        # nothing on transparent pixels or outside the mask
        transparent = alpha.point(lambda v: 255 if v == 0 else 0)
        self.assertIsNone(ImageChops.multiply(gm, transparent).getbbox(), f"{label}: painted on transparency")
        if region is not None:
            outside = region.crop(box_px).point(lambda v: 255 if v == 0 else 0)
            self.assertIsNone(
                ImageChops.multiply(gm, outside).getbbox(), f"{label}: painted outside the mask"
            )

        sil_rows = silhouette.tobytes()
        armhole = composer.armhole(style, index)
        armhole_rows = round(armhole[0] * h / 2) if armhole else 0
        reach = math.ceil(armhole[1] * w) + 1 if armhole else 0

        def in_armhole(y):
            return bool(armhole) and fit.shoulder_row < y <= fit.shoulder_row + 1 + 2 * armhole_rows

        def bare(y, left, right):
            """Silhouette pixels between each edge and the garment edge on one row."""
            got = row_span(gm, y)
            self.assertIsNotNone(got, f"{label}: row {y} has no garment")
            sil = sil_rows[y * w : (y + 1) * w]
            self.assertGreaterEqual(got[0], left - 1, f"{label}: garment beyond the left edge at row {y}")
            self.assertLessEqual(got[1], right + 1, f"{label}: garment beyond the right edge at row {y}")
            return sum(1 for x in range(left, got[0]) if sil[x]), sum(
                1 for x in range(got[1] + 1, right + 1) if sil[x]
            )

        # neckline: no bare gap right under the collar
        row = fit.neck_row + dip + 1
        torso = fit.torso[row]
        sil = sil_rows[row * w : (row + 1) * w]
        covered = gm.tobytes()[row * w : (row + 1) * w]
        for x in range(fit.neck_left, fit.neck_right + 1):
            if sil[x] != 255 or torso is None or not torso[0] + inset <= x <= torso[1] - inset:
                continue
            if in_armhole(row) and not torso[0] + reach <= x <= torso[1] - reach:
                continue  # the armhole, not the collar, opens the side
            self.assertEqual(covered[x], 255, f"{label}: bare gap at column {x} under the collar")

        # shoulders: the garment spans the torso (the whole silhouette for long sleeves) minus the inset
        in_sleeve = span is not None and span[0] <= fit.shoulder_row <= span[1]
        edge = fit.extents[fit.shoulder_row] if in_sleeve else fit.torso[fit.shoulder_row]
        left_bare, right_bare = bare(fit.shoulder_row, *edge)
        self.assertLessEqual(left_bare, inset + 1, f"{label}: left shoulder")
        self.assertLessEqual(right_bare, inset + 1, f"{label}: right shoulder")

        # sides and sleeves, row by row from the shoulders to the hem
        for y in range(fit.shoulder_row, fit.hem_row + 1):
            torso = fit.torso[y]
            if torso is None or in_armhole(y):
                continue
            sleeved = span is not None and span[0] <= y <= span[1]
            left_bare, right_bare = bare(y, *(fit.extents[y] if sleeved else torso))
            self.assertLessEqual(left_bare, inset + 1, f"{label}: bare left side at row {y}")
            self.assertLessEqual(right_bare, inset + 1, f"{label}: bare right side at row {y}")
        if armhole:  # the armhole is cut at the sides below the shoulder row
            y = fit.shoulder_row + 1 + armhole_rows
            got = row_span(gm, y)
            self.assertGreater(got[0] - fit.torso[y][0], 0.5 * armhole[1] * w, f"{label}: no armhole")
            if fit.extents[y][0] < fit.torso[y][0]:  # arm nubs stay bare
                self.assertGreater(got[0], fit.torso[y][0], f"{label}: arm nub painted")

        # hem
        last = max(y for y in range(h) if row_span(gm, y) is not None)
        self.assertGreaterEqual(last, fit.hem_row - 1, f"{label}: the hem stops short")


def fit_cases():
    """(name, source, body box, region, whether the head overlaps the box)."""
    example = Image.open(ROOT / "examples" / "mascot.png").convert("RGBA")
    default_px = pixel_box(DEFAULT_BOX, example.size)
    example_px = pixel_box(EXAMPLE_BODY_BOX, example.size)
    return [
        ("synthetic", head_over_body_image(), SYNTHETIC_BOX, None, False),
        ("synthetic off-centre", head_over_body_image(150), SYNTHETIC_BOX, None, False),
        ("real default box", example, DEFAULT_BOX, None, False),
        ("real example box", example, EXAMPLE_BODY_BOX, None, True),
        ("real default box with mask", example, DEFAULT_BOX, rect_region(example, default_px), False),
        ("real example box with mask", example, EXAMPLE_BODY_BOX, rect_region(example, example_px), True),
    ]


class FitTests(FitAssertions, unittest.TestCase):
    def test_every_garment_fits_the_measured_silhouette(self):
        from mascot_wardrobe.wardrobe import LocalComposer, OutfitBrief

        for name, source, box, region, head in fit_cases():
            for style in ("classic", "streetwear"):
                for index, category in enumerate(CATEGORY_NAMES):
                    with self.subTest(case=name, category=category, style=style):
                        brief = OutfitBrief(category, PROBE, (), box, "fit", style=style, region=region)
                        result = LocalComposer().render(source.copy(), brief)
                        self.assert_fit(
                            source, result, pixel_box(box, source.size), region, index, head=head, style=style
                        )

    def test_prepare_wardrobe_fits_the_real_mascot_in_both_boxes(self):
        from mascot_wardrobe.application import prepare_wardrobe

        source = Image.open(ROOT / "examples" / "mascot.png").convert("RGBA")
        for box, head in ((DEFAULT_BOX, False), (EXAMPLE_BODY_BOX, True)):
            with tempfile.TemporaryDirectory() as temporary:
                manifest = prepare_wardrobe(
                    ROOT / "examples" / "mascot.png", "mascot", "BR", Path(temporary) / "out", body_box=box
                )
                outfits = json.loads(manifest.read_text())["outfits"]
                for index, outfit in enumerate(outfits):
                    with self.subTest(box=box, category=outfit["category"]):
                        result = Image.open(manifest.parent / outfit["png"]).convert("RGBA")
                        self.assert_fit(source, result, pixel_box(box, source.size), None, index, head=head)

    def test_compose_layers_clips_a_full_canvas_layer_to_the_coverage(self):
        from mascot_wardrobe.wardrobe.composer import compose_layers

        coverage = Image.new("L", (40, 30), 0)
        ImageDraw.Draw(coverage).rectangle((10, 5, 29, 24), fill=255)
        layers = [
            Image.new("RGBA", (40, 30), (255, 0, 0, 255)),
            Image.new("RGBA", (40, 30), (0, 0, 255, 128)),
        ]
        piece = compose_layers(coverage, layers)
        self.assertEqual(piece.size, (40, 30))
        self.assertEqual(piece.getchannel("A").getbbox(), (10, 5, 30, 25))
        self.assertEqual(piece.getpixel((0, 0))[3], 0)
        self.assertEqual(piece.getpixel((20, 15))[3], 255)
        extra = Image.new("L", (40, 30), 0)
        ImageDraw.Draw(extra).rectangle((0, 0, 19, 29), fill=255)
        self.assertEqual(compose_layers(coverage, layers, extra).getchannel("A").getbbox(), (10, 5, 20, 25))

    def test_brief_validates_style_and_region(self):
        from mascot_wardrobe.wardrobe import LocalComposer, OutfitBrief

        source = source_image()
        box = (0.22, 0.60, 0.78, 0.88)
        for change in (
            dict(style="grunge"),
            dict(region=Image.new("RGB", source.size)),
            dict(region=Image.new("L", (10, 10))),
            dict(region="mask"),
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                LocalComposer().render(source, OutfitBrief("cold", PROBE, (), box, "t", **change))
        full = Image.new("L", source.size, 255)
        LocalComposer().render(source, OutfitBrief("cold", PROBE, (), box, "t", style="classic", region=full))

    def test_fully_opaque_box_fails_without_a_mask_and_succeeds_with_one(self):
        from mascot_wardrobe.wardrobe import FitError, LocalComposer, OutfitBrief

        opaque = Image.new("RGBA", (160, 200), (200, 200, 200, 255))
        box = (0.22, 0.60, 0.78, 0.88)
        with self.assertRaises(FitError):
            LocalComposer().render(opaque, OutfitBrief("cold", PROBE, (), box, "t"))
        mask = Image.new("L", opaque.size, 0)
        ImageDraw.Draw(mask).ellipse((20, 120, 140, 175), fill=255)
        result = LocalComposer().render(opaque, OutfitBrief("cold", PROBE, (), box, "t", region=mask))
        self.assertIsNotNone(garment_mask(opaque, result).getbbox())

    def test_cache_config_is_template_revision_three(self):
        from mascot_wardrobe.wardrobe import LocalComposer

        self.assertEqual(
            LocalComposer.cache_config,
            {"renderer": "local-composer", "templates": 3, "styles": ["classic", "streetwear"]},
        )

    def test_a_templates_two_manifest_is_rejected_with_a_regenerate_message(self):
        from mascot_wardrobe.wardrobe import WardrobeAgent

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mascot, profile = AgentTests().fixture(root)
            output = root / "out"
            manifest = WardrobeAgent().generate(mascot, profile, output)
            data = json.loads(manifest.read_text())
            data["request"]["renderer_config"] = {"renderer": "local-composer", "templates": 2}
            manifest.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "template set 2.*regenerate.*new output directory"):
                WardrobeAgent().generate(mascot, profile, output)


PALETTE_3 = ("#101014", "#4920A8", "#9B4DFF", "#DDC5FF")


def colour_counts(result, source, colors):
    """Pixels of the result, inside the garment, that exactly match each colour."""
    changed = garment_mask(source, result).tobytes()
    rgb = result.convert("RGB").tobytes()
    wanted = [tuple(int(c[i : i + 2], 16) for i in (1, 3, 5)) for c in colors]
    counts = [0] * len(wanted)
    for i, flag in enumerate(changed):
        if flag:
            pixel = tuple(rgb[3 * i : 3 * i + 3])
            for k, want in enumerate(wanted):
                counts[k] += pixel == want
    return counts


class StreetwearTests(unittest.TestCase):
    def render(self, source, category, style, palette=PALETTE_3, box=DEFAULT_BOX, region=None):
        from mascot_wardrobe.wardrobe import LocalComposer, OutfitBrief

        brief = OutfitBrief(category, palette, (), box, "style", style=style, region=region)
        return LocalComposer().render(source, brief)

    def test_six_garments_in_two_styles_with_palette_three(self):
        source = source_image()
        box = pixel_box(DEFAULT_BOX, source.size)
        hashes = {"classic": set(), "streetwear": set()}
        for category in CATEGORY_NAMES:
            results = {}
            for style in hashes:
                result = self.render(source, category, style)
                results[style] = result
                hashes[style].add(hashlib.sha256(result.tobytes()).hexdigest())
                with self.subTest(category=category, style=style):
                    self.assertEqual(result.getchannel("A").tobytes(), source.getchannel("A").tobytes())
                    expected = source.copy()
                    expected.paste(result.crop(box), box[:2])
                    self.assertEqual(expected.tobytes(), result.tobytes())
                    counts = colour_counts(result, source, PALETTE_3)
                    if style == "streetwear":
                        self.assertTrue(all(c > 0 for c in counts), counts)
                    else:  # classic paints the base and accent roles only
                        self.assertGreater(counts[0], 0)
                        self.assertGreater(counts[3], 0)
                        self.assertEqual((counts[1], counts[2]), (0, 0))
            with self.subTest(category=category):
                self.assertNotEqual(results["classic"].tobytes(), results["streetwear"].tobytes())
        self.assertEqual({len(v) for v in hashes.values()}, {6})

    def test_one_and_two_colour_palettes_stay_inside_the_mask(self):
        for name, source, box, region, _head in fit_cases():
            for palette in (("#336699",), ("#336699", "#ffcc00")):
                for category in CATEGORY_NAMES:
                    with self.subTest(case=name, colours=len(palette), category=category):
                        result = self.render(source.copy(), category, "streetwear", palette, box, region)
                        painted = garment_mask(source, result)
                        px = pixel_box(box, source.size)
                        self.assertIsNotNone(painted.getbbox())
                        outside = Image.new("L", source.size, 255)
                        outside.paste(0, px)
                        self.assertIsNone(ImageChops.multiply(painted, outside).getbbox())
                        transparent = source.getchannel("A").point(lambda v: 255 if v == 0 else 0)
                        self.assertIsNone(ImageChops.multiply(painted, transparent).getbbox())
                        if region is not None:
                            unmasked = region.point(lambda v: 255 if v == 0 else 0)
                            self.assertIsNone(ImageChops.multiply(painted, unmasked).getbbox())

    def test_hood_ring_is_drawn_for_cold_garments_and_never_above_the_neck_row(self):
        from mascot_wardrobe.wardrobe.fit import measure_fit

        for name, source, box, region, _head in fit_cases():
            px = pixel_box(box, source.size)
            fit = measure_fit(source.getchannel("A").crop(px), None if region is None else region.crop(px))
            for index in (0, 1, 2):
                with self.subTest(case=name, index=index):
                    result = self.render(
                        source.copy(), CATEGORY_NAMES[index], "streetwear", box=box, region=region
                    )
                    above = (px[0], px[1], px[2], px[1] + fit.neck_row)
                    self.assertEqual(result.crop(above).tobytes(), source.crop(above).tobytes())
                    self.assertGreater(colour_counts(result, source, [PALETTE_3[1]])[0], 0)

    def test_streetwear_geometry_differs_from_classic(self):
        from mascot_wardrobe.wardrobe import composer
        from mascot_wardrobe.wardrobe.fit import measure_fit

        source = head_over_body_image()
        px = pixel_box(SYNTHETIC_BOX, source.size)
        fit = measure_fit(source.getchannel("A").crop(px), None)
        w = px[2] - px[0]
        self.assertLess(composer.side_inset("streetwear", w), composer.side_inset("classic", w))
        self.assertGreater(
            composer.sleeve_span(3, fit, "streetwear")[0], composer.sleeve_span(3, fit, "classic")[0]
        )
        for index in (4, 5):
            self.assertGreater(
                composer.armhole("streetwear", index)[0], composer.armhole("classic", index)[0]
            )
        self.assertIsNone(composer.armhole("streetwear", 0))


class ChoiceTests(unittest.TestCase):
    """``WardrobeAgent.generate`` with a resolved ``StyleChoice``."""

    PALETTE = ("#101014", "#4920A8", "#9B4DFF", "#DDC5FF")

    def fixture(self, root):
        return AgentTests().fixture(root)

    def choice(self, **overrides):
        from mascot_wardrobe.wardrobe import StyleChoice

        values = {
            "style": "streetwear",
            "palette": self.PALETTE,
            "style_choice": "user",
            "palette_choice": "user",
        }
        values.update(overrides)
        return StyleChoice(**values)

    def test_library_default_is_classic_with_the_profile_palette(self):
        from mascot_wardrobe.wardrobe import WardrobeAgent

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mascot, profile = self.fixture(root)
            manifest = json.loads(WardrobeAgent().generate(mascot, profile, root / "out").read_text())
            request = manifest["request"]
            self.assertEqual(request["style"], "classic")
            self.assertEqual(request["style_choice"], "default")
            self.assertEqual(request["palette_choice"], "profile")
            self.assertEqual(request["palette"], list(profile.palette))

    def test_choice_reaches_the_brief_and_the_request(self):
        from mascot_wardrobe.wardrobe import LocalComposer, WardrobeAgent

        seen = []

        class Spy(LocalComposer):
            def render(self, source, brief):
                seen.append(brief)
                return super().render(source, brief)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mascot, profile = self.fixture(root)
            path = WardrobeAgent(Spy()).generate(
                mascot, profile, root / "out", choice=self.choice(palette_choice="auto")
            )
            manifest = json.loads(path.read_text())
            request = manifest["request"]
            self.assertEqual(request["style"], "streetwear")
            self.assertEqual(request["palette"], list(self.PALETTE))
            self.assertEqual(request["style_choice"], "user")
            self.assertEqual(request["palette_choice"], "auto")
            self.assertEqual(len(seen), 6)
            for brief in seen:
                self.assertEqual(brief.style, "streetwear")
                self.assertEqual(tuple(brief.palette), self.PALETTE)
            for outfit in manifest["outfits"]:
                self.assertIn("#101014", outfit["brief"])
                self.assertNotIn("#2878b5", outfit["brief"])
                self.assertIn("streetwear", outfit["brief"])

    def test_untrusted_choice_is_rejected_before_anything_is_written(self):
        from mascot_wardrobe.wardrobe import WardrobeAgent

        bad = (
            self.choice(style="grunge"),
            self.choice(palette=("#12",)),
            self.choice(palette=()),
            self.choice(palette="#101014"),
            self.choice(style_choice="whatever"),
            self.choice(palette_choice=None),
            self.choice(style=3),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mascot, profile = self.fixture(root)
            for choice in bad:
                with self.assertRaises(ValueError):
                    WardrobeAgent().generate(mascot, profile, root / "out", choice=choice)
                self.assertFalse((root / "out").exists())

    def test_repeat_runs_are_identical_and_other_values_are_refused(self):
        from mascot_wardrobe.wardrobe import WardrobeAgent

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mascot, profile = self.fixture(root)
            out = root / "out"
            agent = WardrobeAgent()
            path = agent.generate(mascot, profile, out, choice=self.choice())
            first = path.read_bytes()
            self.assertEqual(agent.generate(mascot, profile, out, choice=self.choice()), path)
            self.assertEqual(path.read_bytes(), first)
            with self.assertRaises(ValueError):
                agent.generate(mascot, profile, out, choice=self.choice(style="classic"))
            with self.assertRaises(ValueError):
                agent.generate(mascot, profile, out, choice=self.choice(palette=("#101014", "#ffffff")))
            with self.assertRaises(ValueError):
                agent.generate(mascot, profile, out)
            self.assertEqual(path.read_bytes(), first)

    def test_provenance_only_change_rewrites_only_the_manifest(self):
        from mascot_wardrobe.wardrobe import LocalComposer, WardrobeAgent

        calls = []

        class Counting(LocalComposer):
            def render(self, source, brief):
                calls.append(brief.category)
                return super().render(source, brief)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mascot, profile = self.fixture(root)
            out = root / "out"
            agent = WardrobeAgent(Counting())
            path = agent.generate(mascot, profile, out, choice=self.choice())
            assets = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in out.iterdir() if p != path}
            self.assertEqual(len(assets), 18)
            again = agent.generate(
                mascot, profile, out, choice=self.choice(style_choice="auto", palette_choice="auto")
            )
            self.assertEqual(again, path)
            request = json.loads(path.read_text())["request"]
            self.assertEqual((request["style_choice"], request["palette_choice"]), ("auto", "auto"))
            self.assertEqual(len(calls), 6)
            after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in out.iterdir() if p != path}
            self.assertEqual(after, assets)

    def test_provenance_update_needs_verified_assets(self):
        from mascot_wardrobe.wardrobe import WardrobeAgent

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mascot, profile = self.fixture(root)
            out = root / "out"
            path = WardrobeAgent().generate(mascot, profile, out, choice=self.choice())
            before = path.read_bytes()
            (out / json.loads(before)["outfits"][0]["png"]).write_bytes(b"corrupted")
            with self.assertRaises(ValueError):
                WardrobeAgent().generate(mascot, profile, out, choice=self.choice(style_choice="auto"))
            self.assertEqual(path.read_bytes(), before)
