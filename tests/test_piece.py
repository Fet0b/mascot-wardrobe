"""The one-piece-at-a-time session: independent choices per item, targeted revision, add-another loop."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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


class NamedRangeTests(PieceCase):
    def test_named_ranges_save_first_template_without_fabricated_temperature(self):
        result, text, _ = self.session(*piece("cold,mild", "streetwear", "auto"), "n", "n")
        made = result["pieces"][0]
        self.assertEqual(made["ranges"], ["cold", "mild"])
        self.assertEqual(made["template"], "cold")
        self.assertIsNone(made["temperature_c"])
        self.assertNotIn("None", text)
        self.assertIn("mild", self.closet.items()[made["id"]]["description"])

    def test_named_revision_changes_template_and_only_current_piece(self):
        self.closet.add_item("other", "warm", ["#336699"], style="classic")
        before = self.closet.item_path("other").read_bytes()
        result, _, _ = self.session(
            *piece("12", "classic", "#2878b5"), "y", "warm,very_hot", "3", "3", "n", "n"
        )
        made = result["pieces"][0]
        self.assertEqual(made["ranges"], ["warm", "very_hot"])
        self.assertIsNone(made["temperature_c"])
        self.assertEqual(made["template"], "warm")
        self.assertEqual(made["version"], 2)
        self.assertEqual(self.closet.item_path("other").read_bytes(), before)

    def test_named_ranges_are_asked_again_for_each_piece(self):
        result, _, read = self.session(
            *piece("cold,mild", "classic", "#2878b5"),
            "n",
            "y",
            *piece("warm", "streetwear", "auto"),
            "n",
            "n",
        )
        self.assertEqual([made["ranges"] for made in result["pieces"]], [["cold", "mild"], ["warm"]])
        self.assertEqual(read.prompts.count("temperature> "), 2)


class InventorySessionTests(PieceCase):
    def test_saved_piece_has_visible_inventory_number_and_guarded_revision(self):
        calls = []

        def register(closet, item_id, store, **kwargs):
            calls.append((closet.items()[item_id]["sha256"], kwargs))
            return {"number": 7, "status": "imported" if len(calls) == 1 else "updated"}

        with (
            patch(
                "mascot_wardrobe.application.inventory.register_closet_item",
                side_effect=register,
            ),
            patch("mascot_wardrobe.application.inventory.closet_item_id_free", return_value=True),
        ):
            result, text, _ = self.session(
                *piece("cold,mild", "classic", "#2878b5"),
                "y",
                "warm",
                "3",
                "3",
                "n",
                "n",
                store=object(),
            )
        made = result["pieces"][0]
        self.assertEqual(made["inventory"]["number"], 7)
        self.assertIn("#7", text)
        self.assertEqual(calls[0][1]["ranges"], ("cold", "mild"))
        self.assertEqual(calls[1][1], {"ranges": ("warm",), "replaces": calls[0][0]})
        self.assertNotEqual(calls[0][0], calls[1][0])

    def test_range_metadata_revision_keeps_image_and_registers_without_version_bump(self):
        calls = []

        def register(closet, item_id, store, **kwargs):
            calls.append((closet.items()[item_id]["sha256"], kwargs))
            return {"number": 4, "status": "updated"}

        with (
            patch(
                "mascot_wardrobe.application.inventory.register_closet_item",
                side_effect=register,
            ),
            patch("mascot_wardrobe.application.inventory.closet_item_id_free", return_value=True),
        ):
            result, _, _ = self.session(
                *piece("cold", "classic", "#2878b5"),
                "y",
                "cold,mild",
                "3",
                "3",
                "n",
                "n",
                store=object(),
            )
        made = result["pieces"][0]
        self.assertEqual(made["version"], 1)
        self.assertEqual(made["ranges"], ["cold", "mild"])
        self.assertEqual(calls[0][0], calls[1][0])
        self.assertEqual(calls[1][1]["ranges"], ("cold", "mild"))

    def test_inventory_ids_are_skipped_and_custom_collisions_reasked_before_rendering(self):
        def free(closet, item_id, store):
            return item_id not in {"cold-1", "taken"}

        with (
            patch(
                "mascot_wardrobe.application.inventory.register_closet_item",
                return_value={"number": 3, "status": "imported"},
            ),
            patch("mascot_wardrobe.application.inventory.closet_item_id_free", side_effect=free),
            patch(
                "mascot_wardrobe.application.inventory.require_closet_item_id_free",
                side_effect=ValueError("collision: item id taken already registered"),
            ),
        ):
            result, text, read = self.session(
                *piece("cold", "classic", "#2878b5", "taken"),
                "",
                "n",
                "n",
                store=object(),
            )
        self.assertEqual(result["pieces"][0]["id"], "cold-2")
        self.assertIn("item id [cold-2]> ", read.prompts)
        self.assertIn("collision", text)
        self.assertEqual(set(self.closet.items()), {"cold-2"})

    def test_failed_revision_registration_keeps_saved_piece_and_exact_guarded_retry(self):
        hashes = []

        def register(closet, item_id, store, **kwargs):
            hashes.append(closet.items()[item_id]["sha256"])
            if len(hashes) == 1:
                return {"number": 1, "status": "imported"}
            raise ValueError("registration failed")

        with (
            patch(
                "mascot_wardrobe.application.inventory.register_closet_item",
                side_effect=register,
            ),
            patch("mascot_wardrobe.application.inventory.closet_item_id_free", return_value=True),
            self.assertRaisesRegex(ValueError, "registration failed") as caught,
        ):
            self.session(
                *piece("cold", "classic", "#2878b5"),
                "y",
                "warm",
                "3",
                "3",
                store=object(),
            )
        self.assertEqual(self.closet.items()["cold-1"]["version"], 2)
        self.assertIn(
            f"mascot-wardrobe inventory import-closet --root {self.closet.root} "
            f"--item cold-1 --replaces {hashes[0]}",
            str(caught.exception),
        )

    def test_failed_new_registration_keeps_saved_piece_with_import_retry(self):
        with (
            patch(
                "mascot_wardrobe.application.inventory.register_closet_item",
                side_effect=OSError("disk full"),
            ),
            patch("mascot_wardrobe.application.inventory.closet_item_id_free", return_value=True),
            self.assertRaisesRegex(ValueError, "disk full") as caught,
        ):
            self.session(
                *piece("cold", "classic", "#2878b5"),
                store=SimpleNamespace(home=self.base / "inventory data"),
            )
        self.assertTrue(self.closet.item_path("cold-1").is_file())
        self.assertIn(
            f"inventory import-closet --root {self.closet.root} --item cold-1", str(caught.exception)
        )
        self.assertIn(f"MASCOT_WARDROBE_HOME='{self.base / 'inventory data'}' ", str(caught.exception))

    def test_eof_during_revision_reports_already_saved_piece(self):
        with self.assertRaisesRegex(ValueError, "saved piece.*cold-1"):
            self.session(*piece("cold", "classic", "#2878b5"), "y")
        self.assertTrue(self.closet.item_path("cold-1").is_file())


class PersistentPieceTests(PieceCase):
    def test_real_store_keeps_numbers_and_ranges_through_revision_and_another_piece(self):
        from mascot_wardrobe.infrastructure.inventory_store import InventoryStore

        store = InventoryStore.open(self.base / "data")
        result, text, _ = self.session(
            *piece("cold,mild", "classic", "#2878b5"),
            "y",
            "warm",
            "3",
            "3",
            "n",
            "y",
            *piece("cold", "streetwear", "auto"),
            "n",
            "n",
            store=store,
        )
        first, second = result["pieces"]
        self.assertEqual([first["inventory"]["number"], second["inventory"]["number"]], [1, 2])
        self.assertIn("#1", text)
        self.assertIn("#2", text)
        saved = store.read()
        garment = saved.describe(1)
        self.assertEqual(garment.version, 2)
        self.assertEqual(garment.ranges, ("warm", "cold", "mild"))
        self.assertEqual(garment.asset.files[0].sha256, self.digest("cold-1"))
        self.assertEqual(len(garment.origin["history"]), 1)
        self.assertEqual(saved.describe(2).ranges, ("cold",))
        self.assertEqual(dict(saved.defaults), {})

    def test_real_identity_preflight_skips_other_closet_id_and_preserves_first_garment(self):
        from mascot_wardrobe.application.inventory import register_closet_item
        from mascot_wardrobe.infrastructure.inventory_store import InventoryStore

        self.closet.add_item("cold-1", "cold", ["#336699"], style="classic")
        store = InventoryStore.open(self.base / "data")
        register_closet_item(self.closet, "cold-1", store)
        original = store.read().describe(1)
        original_bytes = self.closet.item_path("cold-1").read_bytes()
        self.closet = Closet.init(self.base / "other", self.base / "m.png", self.base / "mask.png")
        result, text, read = self.session(
            *piece("cold", "streetwear", "auto", "cold-1"),
            "",
            "n",
            "n",
            store=store,
        )
        self.assertEqual(result["pieces"][0]["id"], "cold-2")
        self.assertEqual(result["pieces"][0]["inventory"]["number"], 2)
        self.assertIn("collision", text.lower())
        self.assertIn("item id [cold-2]> ", read.prompts)
        self.assertEqual(store.read().describe(1), original)
        self.assertEqual((self.base / "closet" / "items" / "cold-1.png").read_bytes(), original_bytes)
