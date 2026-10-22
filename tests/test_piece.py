"""The one-piece-at-a-time session: independent choices per item, targeted revision, add-another loop."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from mascot_wardrobe.closet import Closet
from mascot_wardrobe.infrastructure.policies import load_profile
from tests.test_closet import write_mascot, write_mask

ROOT = Path(__file__).resolve().parents[1]


def script(*answers):
    queue = list(answers)
    prompts = []

    def read(prompt=""):
        prompts.append(prompt)
        if not queue:
            raise EOFError
        return queue.pop(0)

    read.prompts, read.queue = prompts, queue
    return read


def piece(temperature, style, palette, item_id=""):
    """The answers for one piece: temperature, style, palette, item id (blank = default)."""
    answers = [temperature]
    answers += ["1", style] if style != "auto" else ["2"]
    answers += ["1", palette] if palette != "auto" else ["2"]
    return [*answers, item_id]


class PieceCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        write_mascot(self.base / "m.png")
        write_mask(self.base / "mask.png")
        self.closet = Closet.init(self.base / "closet", self.base / "m.png", self.base / "mask.png")
        self.profile = load_profile("BR")  # thresholds 5, 15, 20, 25, 32

    def session(self, *answers, **kwargs):
        from mascot_wardrobe.interfaces.piece import run_piece_session

        read = script(*answers)
        lines = []
        result = run_piece_session(self.closet, self.profile, read, lines.append, **kwargs)
        self.assertEqual(read.queue, [], "unused answers: the script and the flow disagree")
        return result, "\n".join(lines), read

    def digest(self, item_id):
        return hashlib.sha256(self.closet.item_path(item_id).read_bytes()).hexdigest()


class TemperatureMappingTests(PieceCase):
    def test_boundaries_use_the_profile_thresholds(self):
        cases = (
            ("4.9", "very_cold"),
            ("5", "cold"),
            ("14.9", "cold"),
            ("15", "mild"),
            ("20", "ambient"),
            ("25", "warm"),
            ("31.9", "warm"),
            ("32", "very_hot"),
            ("-90", "very_cold"),
            ("65", "very_hot"),
        )
        for text, category in cases:
            with self.subTest(temperature=text):
                result, _, _ = self.session(*piece(text, "classic", "#2878b5"), "n", "n")
                (made,) = result["pieces"]
                self.assertEqual((made["category"], made["template"]), (category, category))
                self.assertEqual(made["temperature_c"], float(text))
                self.closet.remove_item(made["id"])

    def test_another_country_profile_changes_the_category(self):
        self.profile = load_profile("GB")  # -5, 5, 12, 18, 25
        result, _, _ = self.session(*piece("3", "classic", "#2878b5"), "n", "n")
        self.assertEqual(result["pieces"][0]["category"], "cold")

    def test_out_of_range_temperature_is_rejected_without_saving(self):
        with self.assertRaisesRegex(ValueError, "No valid temperature"):
            self.session("66", "-91", "hot")
        self.assertEqual(self.closet.items(), {})


class IndependentChoiceTests(PieceCase):
    def test_style_and_palette_are_asked_for_every_piece_and_never_inherited(self):
        result, _, read = self.session(
            *piece("10", "streetwear", "#101014 #4920a8 #9b4dff #ddc5ff"),
            "n",
            "y",
            *piece("28", "classic", "#2878b5 #e8b84b"),
            "n",
            "n",
        )
        first, second = result["pieces"]
        self.assertEqual((first["style"], second["style"]), ("streetwear", "classic"))
        self.assertEqual(first["palette"], ["#101014", "#4920a8", "#9b4dff", "#ddc5ff"])
        self.assertEqual(second["palette"], ["#2878b5", "#e8b84b"])
        for noun in ("temperature", "style", "palette"):
            self.assertEqual(read.prompts.count(f"{noun}> "), 2, noun)

    def test_style_user_and_palette_auto_are_independent(self):
        result, _, _ = self.session(*piece("10", "streetwear", "auto"), "n", "n")
        (made,) = result["pieces"]
        self.assertEqual((made["style_choice"], made["palette_choice"]), ("user", "auto"))
        result, _, _ = self.session(*piece("10", "auto", "#336699", "other"), "n", "n")
        made = result["pieces"][0]
        self.assertEqual((made["style_choice"], made["palette_choice"]), ("auto", "user"))

    def test_only_the_current_item_is_rendered(self):
        self.session(*piece("10", "classic", "#2878b5"), "n", "n")
        self.assertEqual(len(self.closet.items()), 1)  # not six


class OutputTests(PieceCase):
    def test_result_and_prompts_show_path_choices_and_provenance(self):
        result, text, _ = self.session(*piece("12", "classic", "#2878b5 #e8b84b"), "n", "n")
        (made,) = result["pieces"]
        self.assertTrue(Path(made["path"]).is_file())
        self.assertEqual(made["sha256"], self.digest(made["id"]))
        self.assertEqual(made["version"], 1)
        self.assertEqual(made["id"], "cold-1")
        self.assertEqual((made["style_choice"], made["palette_choice"]), ("user", "user"))
        self.assertFalse(result["remote_inference"])
        self.assertEqual(result["generator"], "local-compositor")
        for needle in (made["path"], "classic", "#2878b5", "12.0", "cold", "user"):
            self.assertIn(needle, text)
        self.assertIn("12.0", self.closet.items()[made["id"]]["description"])

    def test_default_ids_do_not_collide_and_a_custom_id_is_validated(self):
        result, text, _ = self.session(
            *piece("10", "classic", "#2878b5"),
            "n",
            "y",
            *piece("11", "classic", "#2878b5", "Bad Id")[:-1],
            "Bad Id",
            "cold-1",
            "my-coat",
            "n",
            "n",
        )
        self.assertEqual([p["id"] for p in result["pieces"]], ["cold-1", "my-coat"])
        self.assertIn("already exists", text)


class RevisionTests(PieceCase):
    def test_revision_changes_only_the_current_item(self):
        self.closet.add_item("old", "ambient", ["#aa2222"], style="classic")
        before = json.loads(json.dumps(self.closet.items()["old"]))
        before_bytes = self.closet.item_path("old").read_bytes()
        result, _, _ = self.session(
            *piece("10", "classic", "#2878b5"),
            "y",  # revise
            "",  # keep temperature
            "1",
            "streetwear",  # new style
            "3",  # keep palette
            "n",  # revise again?
            "n",
        )
        (made,) = result["pieces"]
        self.assertEqual((made["version"], made["style"], made["style_choice"]), (2, "streetwear", "user"))
        self.assertEqual(made["palette"], ["#2878b5"])
        self.assertEqual(made["revisions"], 1)
        self.assertEqual(self.closet.items()["old"], before)
        self.assertEqual(self.closet.item_path("old").read_bytes(), before_bytes)
        self.closet.verify()

    def test_revising_the_temperature_changes_template_and_description(self):
        result, _, _ = self.session(*piece("10", "classic", "#2878b5"), "y", "30", "3", "3", "n", "n")
        (made,) = result["pieces"]
        self.assertEqual((made["category"], made["version"]), ("warm", 2))
        self.assertEqual(made["id"], "cold-1")  # the id is the stable slug
        self.assertIn("30.0", self.closet.items()["cold-1"]["description"])

    def test_keeping_everything_reports_nothing_to_change_and_keeps_the_version(self):
        result, text, _ = self.session(*piece("10", "classic", "#2878b5"), "y", "", "3", "3", "n", "n")
        self.assertEqual(result["pieces"][0]["version"], 1)
        self.assertIn("Nothing to change", text)


class LoopTests(PieceCase):
    def test_no_means_finish_without_a_wardrobe(self):
        result, _, _ = self.session(*piece("10", "classic", "#2878b5"), "n", "n")
        self.assertIsNone(result["wardrobe"])
        self.assertEqual(self.closet.wardrobes(), {})

    def test_wardrobe_name_saves_this_sessions_pieces_and_builds_the_catalog(self):
        self.closet.add_item("older", "ambient", ["#aa2222"], style="classic")
        result, _, _ = self.session(
            *piece("10", "classic", "#2878b5"),
            "n",
            "y",
            *piece("28", "streetwear", "auto"),
            "n",
            "n",
            wardrobe="today",
        )
        ids = [entry["id"] for entry in self.closet.wardrobes()["today"]["items"]]
        self.assertEqual(ids, ["cold-1", "warm-1"])
        self.assertTrue(Path(result["wardrobe"]["catalog"]["sheet"]).is_file())

    def test_end_of_input_after_a_saved_piece_keeps_it_and_says_so(self):
        with self.assertRaisesRegex(ValueError, "cold-1"):
            self.session(*piece("10", "classic", "#2878b5"), "n")
        self.assertIn("cold-1", self.closet.items())

    def test_protected_pixels_stay_identical(self):
        self.session(*piece("10", "streetwear", "auto"), "n", "n")
        source = Image.open(self.closet.source_path).convert("RGBA")
        out = Image.open(self.closet.item_path("cold-1")).convert("RGBA")
        mask = Image.open(self.closet.mask_path).convert("L")
        for x in range(source.width):
            for y in range(source.height):
                if mask.getpixel((x, y)) == 0:
                    self.assertEqual(out.getpixel((x, y)), source.getpixel((x, y)))


class SessionCLITests(PieceCase):
    def test_non_interactive_piece_is_refused_with_guidance(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mascot_wardrobe.interfaces.cli",
                "closet",
                "--root",
                str(self.base / "closet"),
                "piece",
                "--country",
                "BR",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("interactive", result.stderr)
        self.assertIn("closet add", result.stderr)

    def test_main_runs_the_session_with_injected_input(self):
        import contextlib
        import io

        from mascot_wardrobe.interfaces.cli import main

        read = script(*piece("10", "classic", "#2878b5"), "n", "n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(
                ["closet", "--root", str(self.base / "closet"), "piece", "--country", "BR"],
                interactive=True,
                read=read,
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["pieces"][0]["id"], "cold-1")
