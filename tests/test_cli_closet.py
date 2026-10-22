import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_closet import write_mascot, write_mask

ROOT = Path(__file__).resolve().parents[1]


class ClosetCLIBase(unittest.TestCase):
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


class ClosetCLITests(ClosetCLIBase):
    def test_full_workflow(self):
        init = self.closet_cmd("init", "--mascot", self.base / "m.png", "--mask", self.base / "mask.png")
        self.assertEqual(len(init["mascot_sha256"]), 64)
        self.closet_cmd(
            "add", "coat", "--template", "very_cold", "--style", "classic", "--palette", "#2878b5", "#e8b84b"
        )
        self.closet_cmd("add", "tee", "--template", "ambient", "--style", "classic", "--palette", "#aa2222")
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
        self.closet_cmd("add", "coat", "--template", "cold", "--style", "classic", "--palette", "#2878b5")
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
            "add", "coat", "--template", "cold", "--style", "classic", "--palette", "#2878b5",
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
        self.closet_cmd("add", "coat", "--template", "cold", "--style", "classic", "--palette", "#2878b5")
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
            "add",
            "coat",
            "--template",
            "cold",
            "--style",
            "classic",
            "--palette",
            "#2878b5",
            "--description",
            "",
        )
        self.assertEqual(added["description"], "")
        self.assertEqual(added["name"], "Coat")

    def test_dash_leading_description_needs_the_equals_form(self):
        self.closet_cmd("init", "--mascot", self.base / "m.png", "--mask", self.base / "mask.png")
        self.closet_cmd("add", "coat", "--template", "cold", "--style", "classic", "--palette", "#2878b5")
        edited = self.closet_cmd("edit", "coat", "--description=-wool")
        self.assertEqual(edited["description"], "-wool")

    def test_malformed_persisted_labels_fail_cleanly_with_exit_2(self):
        self.closet_cmd("init", "--mascot", self.base / "m.png", "--mask", self.base / "mask.png")
        self.closet_cmd("add", "coat", "--template", "cold", "--style", "classic", "--palette", "#2878b5")
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


class ClosetChoiceCLITests(ClosetCLIBase):
    """Style and palette choices on closet add/edit: flags, prompts and non-interactive rules."""

    def setUp(self):
        super().setUp()
        self.closet_cmd("init", "--mascot", self.base / "m.png", "--mask", self.base / "mask.png")

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.closet.rglob("*") if p.is_file()}

    def interactive(self, answers, *args):
        import contextlib
        import io

        from mascot_wardrobe.interfaces.cli import main

        queue = list(answers)

        def read(prompt=""):
            if not queue:
                raise EOFError
            return queue.pop(0)

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["closet", "--root", str(self.closet), *map(str, args)], interactive=True, read=read)
        return code, out.getvalue(), err.getvalue()

    def test_non_interactive_add_requires_explicit_style_and_palette(self):
        before = self.snapshot()
        for args in (
            ("--template", "cold", "--palette", "#2878b5"),
            ("--template", "cold", "--style", "classic"),
            ("--template", "cold"),
        ):
            with self.subTest(args=args):
                result = self.closet_cmd("add", "coat", *args, ok=False)
                self.assertEqual(result.returncode, 2)
                self.assertIn("not interactive", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_explicit_auto_and_values_succeed_with_provenance(self):
        added = self.closet_cmd("add", "coat", "--template", "cold", "--style", "auto", "--palette", "auto")
        self.assertEqual((added["style_choice"], added["palette_choice"]), ("auto", "auto"))
        again = self.closet_cmd(
            "add", "coat2", "--template", "cold", "--style", "streetwear", "--palette", "#2878b5"
        )
        self.assertEqual(
            (again["style"], again["style_choice"], again["palette_choice"]), ("streetwear", "user", "user")
        )
        listing = self.closet_cmd("list")["items"]
        self.assertEqual(listing["coat2"]["style"], "streetwear")

    def test_invalid_values_exit_2_without_changes(self):
        before = self.snapshot()
        for args in (
            ("--style", "grunge", "--palette", "#2878b5"),
            ("--style", "classic", "--palette", "auto", "#101014"),
            ("--style", "classic", "--palette", "#12"),
            ("--style", "classic", "--palette", "red"),
        ):
            with self.subTest(args=args):
                result = self.closet_cmd("add", "coat", "--template", "cold", *args, ok=False)
                self.assertEqual(result.returncode, 2)
        self.assertEqual(self.snapshot(), before)

    def test_template_stays_required_on_add_and_help_documents_roles(self):
        result = self.closet_cmd("add", "coat", "--style", "classic", "--palette", "auto", ok=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--template", result.stderr)
        helps = self.run_cli("closet", "add", "--help").stdout
        for word in ("--style", "base", "secondary", "accent", "highlight", "auto", "deterministic"):
            self.assertIn(word, helps)

    def test_add_prompts_for_missing_values_on_stderr(self):
        code, out, err = self.interactive(["2", "1", "#101014 #4920A8"], "add", "coat", "--template", "cold")
        self.assertEqual(code, 0, err)
        item = json.loads(out)
        self.assertEqual((item["style_choice"], item["palette_choice"]), ("auto", "user"))
        self.assertIn("style", err)
        self.assertIn("palette", err)

    def test_add_prompt_end_of_input_writes_nothing(self):
        before = self.snapshot()
        code, _, err = self.interactive([], "add", "coat", "--template", "cold")
        self.assertEqual(code, 2)
        self.assertIn("end of input", err)
        self.assertEqual(self.snapshot(), before)

    def test_no_input_forces_the_non_interactive_rules(self):
        import contextlib
        import io

        from mascot_wardrobe.interfaces.cli import main

        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = main(
                ["--no-input", "closet", "--root", str(self.closet), "add", "coat", "--template", "cold"],
                interactive=True,
                read=lambda prompt="": self.fail("prompted"),
            )
        self.assertEqual(code, 2)

    def add_coat(self):
        return self.closet_cmd(
            "add", "coat", "--template", "cold", "--style", "classic", "--palette", "#2878b5"
        )

    def test_edit_style_bumps_the_version(self):
        self.add_coat()
        edited = self.closet_cmd("edit", "coat", "--style", "streetwear")
        self.assertEqual(
            (edited["version"], edited["style"], edited["palette"]), (2, "streetwear", ["#2878b5"])
        )

    def test_non_interactive_edit_keeps_stored_values(self):
        added = self.add_coat()
        edited = self.closet_cmd("edit", "coat", "--template", "warm")
        self.assertEqual((edited["style"], edited["palette"]), ("classic", ["#2878b5"]))
        self.assertEqual(edited["version"], added["version"] + 1)

    def test_identical_edit_is_nothing_to_change(self):
        self.add_coat()
        result = self.closet_cmd("edit", "coat", "--style", "classic", "--palette", "#2878b5", ok=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Nothing to change", result.stderr)

    def test_label_only_edit_never_prompts(self):
        self.add_coat()
        code, out, err = self.interactive([], "edit", "coat", "--name", "Parka")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["name"], "Parka")
        self.assertEqual(err, "")

    def test_garment_edit_prompts_with_keep_current(self):
        self.add_coat()
        code, out, err = self.interactive(["3", "3"], "edit", "coat", "--template", "warm")
        self.assertEqual(code, 0, err)
        self.assertIn("keep current", err)
        item = json.loads(out)
        self.assertEqual((item["template"], item["style"], item["palette"]), ("warm", "classic", ["#2878b5"]))
        code, out, err = self.interactive(["2"], "edit", "coat", "--style", "streetwear")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["palette_choice"], "auto")  # only the palette was asked


class ClosetMigrateCLITests(ClosetCLIBase):
    def legacy(self):
        self.closet_cmd("init", "--mascot", self.base / "m.png", "--mask", self.base / "mask.png")
        self.closet_cmd("add", "coat", "--template", "cold", "--style", "classic", "--palette", "#2878b5")
        path = self.closet / "closet.json"
        data = json.loads(path.read_text())
        data["renderer"] = {"renderer": "local-composer", "templates": 2}
        for key in ("style", "style_choice", "palette_choice"):
            data["items"]["coat"].pop(key)
        path.write_text(json.dumps(data))
        return path

    def test_open_message_then_dry_run_then_migrate(self):
        path = self.legacy()
        result = self.closet_cmd("list", ok=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("migrate", result.stderr)
        original = path.read_bytes()
        plan = self.closet_cmd("migrate", "--dry-run")
        self.assertEqual((plan["migrated"], plan["dry_run"], plan["items"]), (False, True, ["coat"]))
        self.assertEqual(path.read_bytes(), original)
        report = self.closet_cmd("migrate")
        self.assertTrue(report["migrated"])
        self.assertEqual(self.closet_cmd("list")["items"]["coat"]["style_choice"], "migrated")
        self.assertEqual(self.closet_cmd("migrate"), {"migrated": False, "reason": "already current"})
