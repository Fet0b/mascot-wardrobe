"""Publish use case: dry-run default, one selection, same JPEG to every configured channel."""

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from mascot_wardrobe import application


class FakeWhatsApp:
    def __init__(self):
        self.synced = []

    def sync(self, slug):
        self.synced.append(slug)
        return {
            "channel": "whatsapp-hermes-bridge",
            "action": "updated",
            "acknowledged": True,
            "verified": True,
        }


class FakeTelegram:
    def __init__(self):
        self.applied = []

    def apply(self, jpg):
        self.applied.append(Path(jpg).read_bytes())
        return {"channel": "telegram-bot", "acknowledged": True, "verified": False}


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / "mascot.png"
        Image.new("RGBA", (100, 100), "#555555").save(source)
        manifest = application.prepare_wardrobe(source, "pet", "BR", self.root / "catalog")
        self.selection = application.select_outfit(manifest, "BR", 25)
        self.approved = self.root / "approved"
        self.approved.mkdir()
        self.state = self.root / "state"

    def targets(self):
        self.whatsapp, self.telegram = FakeWhatsApp(), FakeTelegram()
        return {
            "whatsapp": application.WhatsAppTarget(self.approved, self.whatsapp),
            "telegram": application.TelegramTarget(self.telegram, self.state),
        }

    def test_dry_run_is_default_and_touches_nothing(self):
        result = application.publish_outfit(self.selection, **self.targets())
        self.assertEqual(result["mode"], "dry-run")
        self.assertEqual(result["atlas"], self.selection["atlas"])
        self.assertEqual(result["jpg"], self.selection["jpg"])
        self.assertEqual(self.whatsapp.synced, [])
        self.assertEqual(self.telegram.applied, [])
        self.assertEqual(list(self.approved.iterdir()), [])
        self.assertFalse(self.state.exists())
        self.assertEqual(result["channels"]["whatsapp"]["status"], "planned")
        self.assertEqual(result["channels"]["telegram"]["status"], "planned")

    def test_apply_delivers_the_same_selected_jpeg_to_both_channels(self):
        result = application.publish_outfit(self.selection, apply=True, **self.targets())
        jpg = Path(self.selection["jpg"]).read_bytes()
        self.assertEqual(result["mode"], "apply")
        self.assertEqual((self.approved / (self.selection["slug"] + ".jpg")).read_bytes(), jpg)
        self.assertEqual(self.whatsapp.synced, [self.selection["slug"]])
        self.assertEqual(self.telegram.applied, [jpg])
        self.assertTrue(result["channels"]["whatsapp"]["verified"])
        self.assertFalse(result["channels"]["telegram"]["verified"])
        self.assertEqual(result["atlas"], self.selection["atlas"])

    def test_telegram_is_idempotent_by_image_hash_and_force_overrides(self):
        application.publish_outfit(self.selection, apply=True, **self.targets())
        again = application.publish_outfit(self.selection, apply=True, **self.targets())
        self.assertEqual(self.telegram.applied, [])
        self.assertEqual(again["channels"]["telegram"]["status"], "unchanged")
        forced = application.publish_outfit(self.selection, apply=True, force=True, **self.targets())
        self.assertEqual(len(self.telegram.applied), 1)
        self.assertEqual(forced["channels"]["telegram"]["status"], "applied")

    def test_apply_without_a_channel_is_refused_and_one_failure_does_not_hide_the_other(self):
        with self.assertRaisesRegex(ValueError, "channel"):
            application.publish_outfit(self.selection, apply=True)
        targets = self.targets()

        def boom(slug):
            raise RuntimeError("bridge down")

        self.whatsapp.sync = boom
        result = application.publish_outfit(self.selection, apply=True, **targets)
        self.assertEqual(result["channels"]["whatsapp"]["status"], "failed")
        self.assertEqual(result["channels"]["whatsapp"]["error"], "bridge down")
        self.assertEqual(result["channels"]["telegram"]["status"], "applied")
        self.assertFalse(result["ok"])


class StateSafetyTests(PublishTests):
    def test_symlinked_state_file_is_refused_and_target_untouched(self):
        self.state.mkdir()
        sentinel = self.root / "sentinel.json"
        sentinel.write_text("keep", encoding="utf-8")
        (self.state / "telegram-profile.json").symlink_to(sentinel)
        result = application.publish_outfit(self.selection, apply=True, telegram=self.targets()["telegram"])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertEqual(result["channels"]["telegram"]["status"], "failed")
        self.assertEqual(self.telegram.applied, [])

    def test_symlinked_state_directory_is_refused(self):
        real = self.root / "real"
        real.mkdir()
        self.state.symlink_to(real)
        result = application.publish_outfit(self.selection, apply=True, telegram=self.targets()["telegram"])
        self.assertEqual(result["channels"]["telegram"]["status"], "failed")
        self.assertEqual(list(real.iterdir()), [])

    def test_state_is_written_atomically_without_leftovers(self):
        application.publish_outfit(self.selection, apply=True, telegram=self.targets()["telegram"])
        self.assertEqual([p.name for p in self.state.iterdir()], ["telegram-profile.json"])

    def test_corrupt_state_does_not_crash_and_is_replaced(self):
        self.state.mkdir()
        (self.state / "telegram-profile.json").write_text("{not json", encoding="utf-8")
        result = application.publish_outfit(self.selection, apply=True, telegram=self.targets()["telegram"])
        self.assertEqual(result["channels"]["telegram"]["status"], "applied")
