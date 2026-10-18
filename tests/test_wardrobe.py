"""Real tests for the generation context."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


def source_image():
    image = Image.new("RGBA", (160, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((28, 15, 132, 180), fill=(130, 180, 210, 255))
    draw.ellipse((60, 50, 70, 60), fill="black")
    draw.ellipse((92, 50, 102, 60), fill="black")
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
