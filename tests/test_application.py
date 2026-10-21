import json
import tempfile
import unittest
from pathlib import Path

from mascot_wardrobe.infrastructure.policies import load_profile
from tests.imaging import write_body_png


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_sandbox_uses_prepared_outfits_real_http_and_repeated_sync_noop(self):
        from mascot_wardrobe import application

        self.assertTrue(hasattr(application, "sync_sandbox"), "Missing sandbox integration use case")
        source = self.root / "mascot.png"
        write_body_png(source)
        manifest = application.prepare_wardrobe(source, "pet", "BR", self.root / "catalog")
        selected = application.select_outfit(manifest, "BR", 25)
        state = self.root / "state"
        first = application.sync_sandbox(selected, state)
        stamp = (state / "current.jpg").stat().st_mtime_ns
        second = application.sync_sandbox(selected, state)
        self.assertEqual(first["action"], "updated")
        self.assertTrue(first["verified"])
        self.assertTrue(first["sandbox"])
        self.assertEqual(second["action"], "unchanged")
        self.assertEqual((state / "current.jpg").stat().st_mtime_ns, stamp)
        self.assertEqual((state / "current.jpg").read_bytes(), Path(selected["jpg"]).read_bytes())

    def test_other_channels_export_only_and_verify_local_bytes(self):
        from mascot_wardrobe import application

        self.assertTrue(hasattr(application, "export_outfit"), "Missing export integration use case")
        source = self.root / "mascot.png"
        write_body_png(source)
        manifest = application.prepare_wardrobe(source, "pet", "BR", self.root / "catalog")
        selected = application.select_outfit(manifest, "BR", 25)
        for channel in ("telegram", "discord", "hermes-cli"):
            result = application.export_outfit(selected, self.root / "exports", channel)
            self.assertFalse(result["remote_sync"])
            self.assertFalse(result["verified"])
            destination = self.root / "exports" / channel / selected["slug"]
            self.assertEqual(
                (destination / Path(selected["jpg"]).name).read_bytes(), Path(selected["jpg"]).read_bytes()
            )

    def test_selection_rejects_manifest_traversal_and_malformed_categories(self):
        from mascot_wardrobe import application

        source = self.root / "mascot.png"
        write_body_png(source)
        manifest = application.prepare_wardrobe(source, "pet", "BR", self.root / "catalog")
        original = json.loads(manifest.read_text())
        original["outfits"][0]["category"] = []
        manifest.write_text(json.dumps(original))
        with self.assertRaises(ValueError):
            application.select_outfit(manifest, "BR", 25)
        data = json.loads(manifest.read_text())
        data["outfits"][0]["category"] = "very_cold"
        selected = next(o for o in data["outfits"] if o["category"] == "warm")
        selected["jpg"] = "../outside.jpg"
        manifest.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            application.select_outfit(manifest, "BR", 25)

    def test_prepare_then_select_fahrenheit_from_explicit_country(self):
        from mascot_wardrobe import application

        self.assertTrue(hasattr(application, "prepare_wardrobe"), "Missing application preparation use case")
        source = self.root / "mascot.png"
        write_body_png(source)
        manifest = application.prepare_wardrobe(
            source, "pet", "BR", self.root / "catalog", body_box=(0.2, 0.5, 0.8, 0.9)
        )
        selection = application.select_outfit(manifest, "BR", 77, "F")
        self.assertEqual(selection["category"], load_profile("BR").classify(25).value)
        self.assertEqual(selection["temperature_c"], 25)
        self.assertTrue(Path(selection["jpg"]).is_file())
        self.assertEqual(selection["country_code"], "BR")
        with self.assertRaises(ValueError):
            application.select_outfit(manifest, "US", 25)

    def test_output_paths_reject_traversal_and_symlinked_ancestors(self):
        from mascot_wardrobe import application

        self.assertFalse(hasattr(application, "PROJECT_ROOT"))
        self.assertFalse(hasattr(application, "SCRATCH_ROOT"))
        source = self.root / "mascot.png"
        write_body_png(source)
        resolved = application.safe_output(self.root / "plain" / "catalog")
        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved, (self.root / "plain" / "catalog").resolve())
        traversal = self.root / "a" / ".." / "escaped"
        with self.assertRaises(ValueError):
            application.prepare_wardrobe(source, "pet", "BR", traversal)
        self.assertFalse((self.root / "escaped").exists())
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaises(ValueError):
            application.prepare_wardrobe(source, "pet", "BR", link / "catalog")
        with self.assertRaises(ValueError):
            application.safe_output(link / "state")
        self.assertEqual(list(real.iterdir()), [])
