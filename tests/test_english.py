import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "mascot_wardrobe"
ACCENTED = re.compile("[áàâãäéèêíìîóòôõöúùûçñÁÀÂÃÉÊÍÓÔÕÚÇ]")


class EnglishTextTests(unittest.TestCase):
    def test_category_labels_are_english(self):
        from mascot_wardrobe.domain.models import Category

        self.assertEqual(
            [c.label for c in Category],
            ["very cold", "cold", "mild", "ambient", "warm", "very hot"],
        )

    def test_profile_notice_and_garments_are_english(self):
        data = json.loads((SRC / "infrastructure" / "profiles.json").read_text(encoding="utf-8"))
        self.assertEqual(
            data["profiles"]["BR"]["garments"][:3], ["padded coat", "closed jacket", "light cardigan"]
        )
        self.assertIsNone(ACCENTED.search(json.dumps(data, ensure_ascii=False)))
        self.assertIn("Adjustable", data["notice"])

    def test_source_files_contain_no_portuguese_text(self):
        for path in SRC.rglob("*.py"):
            with self.subTest(path=path.name):
                self.assertIsNone(ACCENTED.search(path.read_text(encoding="utf-8")), path)

    def test_representative_runtime_messages_are_english(self):
        from mascot_wardrobe.channels import FileExportChannel, WhatsAppBridgeChannel

        with self.assertRaisesRegex(ValueError, "Invalid export channel"):
            FileExportChannel(ROOT, "nope")
        with self.assertRaisesRegex(ValueError, "Invalid slug"):
            WhatsAppBridgeChannel("http://127.0.0.1:8888").sync("../x")
