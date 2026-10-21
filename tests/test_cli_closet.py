import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_closet import write_mascot, write_mask

ROOT = Path(__file__).resolve().parents[1]


class ClosetCLITests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        write_mascot(self.base / "m.png")
        write_mask(self.base / "mask.png")
        self.closet = self.base / "closet"

    def run_cli(self, *args):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        return subprocess.run(
            [sys.executable, "-m", "mascot_wardrobe.interfaces.cli", *map(str, args)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def closet_cmd(self, *args, ok=True):
        result = self.run_cli("closet", "--root", self.closet, *args)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        return result

    def test_full_workflow(self):
        init = self.closet_cmd("init", "--mascot", self.base / "m.png", "--mask", self.base / "mask.png")
        self.assertEqual(len(init["mascot_sha256"]), 64)
        self.closet_cmd("add", "coat", "--template", "very_cold", "--palette", "#2878b5", "#e8b84b")
        self.closet_cmd("add", "tee", "--template", "ambient", "--palette", "#aa2222")
        edited = self.closet_cmd("edit", "tee", "--palette", "#22aa66")
        self.assertEqual(edited["version"], 2)
        self.closet_cmd("save", "mix", "--items", "coat", "tee")
        self.assertEqual(self.closet_cmd("use", "mix")["name"], "mix")
        listing = self.closet_cmd("list")
        self.assertEqual(sorted(listing["items"]), ["coat", "tee"])
        self.assertEqual(listing["active"], "mix")
        catalog = self.closet_cmd("catalog")
        self.assertTrue(Path(catalog["sheet"]).is_file())
        self.assertEqual(len(catalog["tiles"]), 2)
        self.assertTrue(self.closet_cmd("verify")["ok"])
        self.closet_cmd("save", "solo", "--items", "coat")
        self.closet_cmd("save", "mix", "--items", "coat")
        self.closet_cmd("remove", "tee")

    def test_errors_exit_2_and_tampering_is_reported(self):
        self.closet_cmd("init", "--mascot", self.base / "m.png", "--mask", self.base / "mask.png")
        self.closet_cmd("add", "coat", "--template", "cold", "--palette", "#2878b5")
        (self.closet / "mascot" / "source.png").write_bytes(b"swapped")
        result = self.closet_cmd("verify", ok=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("changed", result.stderr)
        again = self.run_cli(
            "closet",
            "--root",
            self.closet,
            "init",
            "--mascot",
            self.base / "m.png",
            "--mask",
            self.base / "mask.png",
        )
        self.assertEqual(again.returncode, 2)

    def test_init_requires_a_mask_and_help_documents_the_identity_lock(self):
        missing = self.run_cli("closet", "--root", self.closet, "init", "--mascot", self.base / "m.png")
        self.assertEqual(missing.returncode, 2)
        self.assertIn("--mask", missing.stderr)
        helps = self.run_cli("closet", "--help").stdout + self.run_cli("closet", "init", "--help").stdout
        for word in ("immutable", "mask", "wardrobe", "catalog", "sha256"):
            self.assertIn(word, helps.lower())
        self.assertIn("closet", self.run_cli("--help").stdout)

    def test_name_and_description_on_add_edit_list_and_catalog(self):
        self.closet_cmd("init", "--mascot", self.base / "m.png", "--mask", self.base / "mask.png")
        added = self.closet_cmd(
            "add", "coat", "--template", "cold", "--palette", "#2878b5",
            "--name", "Winter Coat", "--description", "Wool, double lined.",
        )  # fmt: skip
        self.assertEqual((added["name"], added["description"]), ("Winter Coat", "Wool, double lined."))
        self.closet_cmd("edit", "coat", "--name", "Parka")
        edited = self.closet_cmd("edit", "coat", "--description", "Hooded.")
        self.assertEqual((edited["version"], edited["sha256"]), (1, added["sha256"]))
        item = self.closet_cmd("list")["items"]["coat"]
        self.assertEqual((item["name"], item["description"]), ("Parka", "Hooded."))
        self.closet_cmd("save", "w", "--items", "coat")
        tile = self.closet_cmd("catalog", "--wardrobe", "w")["tiles"][0]
        self.assertEqual((tile["name"], tile["description"]), ("Parka", "Hooded."))
        self.closet_cmd("edit", "coat", "--description", "")
        self.assertEqual(self.closet_cmd("list")["items"]["coat"]["description"], "")

    def test_over_long_text_is_rejected_with_exit_2_and_help_documents_limits(self):
        self.closet_cmd("init", "--mascot", self.base / "m.png", "--mask", self.base / "mask.png")
        self.closet_cmd("add", "coat", "--template", "cold", "--palette", "#2878b5")
        for flag, size in (("--name", 61), ("--description", 501)):
            result = self.closet_cmd("edit", "coat", flag, "x" * size, ok=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn("too long", result.stderr)
        self.assertEqual(self.closet_cmd("list")["items"]["coat"]["name"], "Coat")
        helps = (
            self.run_cli("closet", "add", "--help").stdout + self.run_cli("closet", "edit", "--help").stdout
        )
        for word in ("--name", "--description", "60", "500"):
            self.assertIn(word, helps)

    def test_empty_description_on_add_is_accepted(self):
        self.closet_cmd("init", "--mascot", self.base / "m.png", "--mask", self.base / "mask.png")
        added = self.closet_cmd(
            "add", "coat", "--template", "cold", "--palette", "#2878b5", "--description", ""
        )
        self.assertEqual(added["description"], "")
        self.assertEqual(added["name"], "Coat")

    def test_dash_leading_description_needs_the_equals_form(self):
        self.closet_cmd("init", "--mascot", self.base / "m.png", "--mask", self.base / "mask.png")
        self.closet_cmd("add", "coat", "--template", "cold", "--palette", "#2878b5")
        edited = self.closet_cmd("edit", "coat", "--description=-wool")
        self.assertEqual(edited["description"], "-wool")

    def test_malformed_persisted_labels_fail_cleanly_with_exit_2(self):
        self.closet_cmd("init", "--mascot", self.base / "m.png", "--mask", self.base / "mask.png")
        self.closet_cmd("add", "coat", "--template", "cold", "--palette", "#2878b5")
        self.closet_cmd("save", "w", "--items", "coat")
        self.closet_cmd("use", "w")
        path = self.closet / "closet.json"
        good = path.read_text()
        for field, value in (("name", None), ("description", 5), ("name", "a\nb"), ("name", "n" * 61)):
            data = json.loads(good)
            data["items"]["coat"][field] = value
            path.write_text(json.dumps(data))
            for command in ("list", "catalog", "verify"):
                with self.subTest(field=field, value=value, command=command):
                    result = self.closet_cmd(command, ok=False)
                    self.assertEqual(result.returncode, 2)
                    self.assertTrue(result.stderr.startswith("error:"), result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
