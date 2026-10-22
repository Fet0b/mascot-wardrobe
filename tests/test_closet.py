"""Identity-locked closet: one immutable mascot, inventory, named wardrobes and catalog sheet."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from tests.test_wardrobe import (
    DEFAULT_BOX,
    EXAMPLE_BODY_BOX,
    PROBE,
    ROOT,
    FitAssertions,
    pixel_box,
    rect_region,
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_mascot(path):
    """A body ellipse the default mask box measures well, with two eyes above the mask."""
    image = Image.new("RGBA", (160, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((10, 70, 150, 185), fill=(130, 180, 210, 255))
    draw.ellipse((60, 90, 70, 100), fill="black")
    draw.ellipse((92, 90, 102, 100), fill="black")
    image.save(path)


def write_mask(path, box=(30, 110, 130, 175), size=(160, 200)):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rectangle(box, fill=255)
    mask.save(path)


class ClosetCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.mascot = self.base / "mascot.png"
        self.mask = self.base / "mask.png"
        write_mascot(self.mascot)
        write_mask(self.mask)
        self.root = self.base / "closet"

    def make(self):
        from mascot_wardrobe.closet import Closet

        return Closet.init(self.root, self.mascot, self.mask)


class IdentityTests(ClosetCase):
    def test_init_copies_source_byte_identical_and_records_hashes(self):
        closet = self.make()
        self.assertEqual(sha(closet.source_path), sha(self.mascot))
        data = json.loads((self.root / "closet.json").read_text())
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["mascot"]["sha256"], sha(self.mascot))
        self.assertEqual(data["mask"]["sha256"], sha(self.mask))

    def test_init_requires_a_valid_explicit_mask(self):
        from mascot_wardrobe.closet import Closet

        wrong_size = self.base / "small.png"
        write_mask(wrong_size, (1, 1, 20, 20), (50, 50))
        empty = self.base / "empty.png"
        Image.new("L", (160, 200), 0).save(empty)
        tiny = self.base / "tiny.png"
        write_mask(tiny, (10, 10, 12, 12))
        soft = self.base / "soft.png"
        Image.linear_gradient("L").resize((160, 200)).save(soft)  # values other than 0/255
        for bad in (None, self.base / "missing.png", wrong_size, empty, tiny, soft):
            with self.subTest(mask=bad), self.assertRaises(ValueError):
                Closet.init(self.base / f"c-{bad.name if bad else 'none'}", self.mascot, bad)

    def test_init_refuses_to_reuse_an_existing_closet(self):
        self.make()
        with self.assertRaises(ValueError):
            self.make()

    def test_items_keep_protected_pixels_byte_identical_and_edit_inside_mask(self):
        closet = self.make()
        closet.add_item("coat", "very_cold", ("#2878b5", "#e8b84b"))
        source = Image.open(self.mascot).convert("RGBA")
        out = Image.open(closet.item_path("coat")).convert("RGBA")
        mask = Image.open(self.mask)
        self.assertEqual(out.size, source.size)
        protected = Image.eval(mask, lambda v: 255 if v == 0 else 0)
        blank = Image.new("RGBA", source.size)
        self.assertEqual(
            Image.composite(out, blank, protected).tobytes(),
            Image.composite(source, blank, protected).tobytes(),
        )
        self.assertNotEqual(out.tobytes(), source.tobytes())

    def test_a_mask_reaching_the_image_edges_still_renders(self):
        from mascot_wardrobe.closet import Closet

        mascot = ROOT / "examples" / "mascot.png"
        width, height = Image.open(mascot).size
        edge = self.base / "edge.png"
        box = (width // 10, height * 55 // 100, width - 1, height - 1)  # touches right and bottom edges
        write_mask(edge, box, (width, height))
        closet = Closet.init(self.base / "edge-closet", mascot, edge)
        self.assertEqual(closet._data["mask"]["bbox"], [box[0], box[1], width, height])
        item = closet.add_item("coat", "cold", ("#2878b5",))
        out = Image.open(closet.item_path("coat")).convert("RGBA")
        self.assertNotEqual(out.tobytes(), Image.open(mascot).convert("RGBA").tobytes())
        self.assertEqual(item["version"], 1)

    def test_unmasked_hole_inside_the_box_stays_untouched(self):
        closet_mask = self.base / "half.png"
        mask = Image.new("L", (160, 200), 0)
        ImageDraw.Draw(mask).rectangle((30, 110, 130, 175), fill=255)
        ImageDraw.Draw(mask).rectangle((30, 110, 80, 175), fill=0)
        mask.save(closet_mask)
        from mascot_wardrobe.closet import Closet

        closet = Closet.init(self.root, self.mascot, closet_mask)
        closet.add_item("tee", "ambient", ("#aa2222", "#ffffff"))
        out = Image.open(closet.item_path("tee")).convert("RGBA")
        source = Image.open(self.mascot).convert("RGBA")
        self.assertEqual(out.crop((0, 0, 80, 200)).tobytes(), source.crop((0, 0, 80, 200)).tobytes())

    def test_changed_mascot_or_mask_is_detected_everywhere(self):
        for target, name in (("mascot/source.png", "mascot"), ("mask/edit-mask.png", "mask")):
            with self.subTest(target=target):
                self.setUp()
                closet = self.make()
                closet.add_item("coat", "cold", ("#2878b5",))
                path = self.root / target
                Image.new("RGBA" if name == "mascot" else "L", (160, 200), 7).save(path)
                from mascot_wardrobe.closet import Closet

                for call in (
                    lambda: Closet.open(self.root),
                    lambda closet=closet: closet.add_item("hat", "cold", ("#111111",)),
                    lambda closet=closet: closet.build_catalog(),
                ):
                    with self.assertRaisesRegex(ValueError, "changed|mismatch"):
                        call()

    def test_tampered_item_asset_is_detected(self):
        from mascot_wardrobe.closet import Closet

        closet = self.make()
        closet.add_item("coat", "cold", ("#2878b5",))
        closet.item_path("coat").write_bytes(b"not the saved asset")
        with self.assertRaisesRegex(ValueError, "changed|mismatch"):
            Closet.open(self.root)

    def test_redirected_item_file_field_is_rejected_and_never_read(self):
        from mascot_wardrobe.closet import Closet

        closet = self.make()
        closet.add_item("coat", "cold", ("#2878b5",))
        closet.save_wardrobe("trip", ["coat"])
        alternate = self.base / "alternate.png"  # inside this test's temp dir only
        Image.new("RGBA", (160, 200), (255, 0, 0, 255)).save(alternate)
        path = self.root / "closet.json"
        original = path.read_text()
        for redirect in ("../alternate.png", str(alternate), "items/../items/coat.png", "items\\coat.png"):
            with self.subTest(file=redirect):
                data = json.loads(original)
                data["items"]["coat"]["file"] = redirect
                path.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, "file"):
                    Closet.open(self.root)
                closet._data["items"]["coat"]["file"] = redirect  # already-open instance
                with self.assertRaisesRegex(ValueError, "file"):
                    closet.build_catalog("trip")
        self.assertFalse((self.root / "catalog" / "trip.png").exists())

    def test_catalog_manifest_uses_the_canonical_asset_path(self):
        closet = self.make()
        closet.add_item("coat", "cold", ("#2878b5",))
        closet.save_wardrobe("trip", ["coat"])
        result = closet.build_catalog("trip")
        self.assertEqual(result["tiles"][0]["asset"], "items/coat.png")

    def test_catalog_rejects_an_item_swapped_after_the_last_verify(self):
        closet = self.make()
        closet.add_item("coat", "cold", ("#2878b5",))
        closet.save_wardrobe("trip", ["coat"])
        item = self.root / "items" / "coat.png"
        checked = closet._checked_wardrobe

        def swap_after_validation(name):
            wardrobe = checked(name)  # the final verify() has just passed
            Image.new("RGBA", (160, 200), (255, 0, 0, 255)).save(item)
            return wardrobe

        closet._checked_wardrobe = swap_after_validation
        with self.assertRaisesRegex(ValueError, "changed"):
            closet.build_catalog("trip")
        self.assertFalse((self.root / "catalog" / "trip.png").exists())
        self.assertFalse((self.root / "catalog" / "trip.json").exists())


class InventoryTests(ClosetCase):
    def test_add_edit_remove_items_with_versions(self):
        closet = self.make()
        first = closet.add_item("coat", "cold", ("#2878b5", "#e8b84b"))
        self.assertEqual(first["version"], 1)
        with self.assertRaises(ValueError):
            closet.add_item("coat", "cold", ("#111111",))
        before = sha(closet.item_path("coat"))
        edited = closet.edit_item("coat", palette=("#aa2222", "#ffffff"))
        self.assertEqual(edited["version"], 2)
        self.assertNotEqual(sha(closet.item_path("coat")), before)
        self.assertEqual(edited["sha256"], sha(closet.item_path("coat")))
        closet.remove_item("coat")
        self.assertFalse(closet.item_path("coat").exists())
        self.assertEqual(closet.items(), {})
        with self.assertRaises(ValueError):
            closet.edit_item("coat", template="warm")
        with self.assertRaises(ValueError):
            closet.remove_item("coat")

    def test_item_validation_and_unsafe_ids(self):
        closet = self.make()
        for item_id in ("../x", "Coat", "", "a/b", "x" * 80):
            with self.subTest(item_id=item_id), self.assertRaises(ValueError):
                closet.add_item(item_id, "cold", ("#111111",))
        with self.assertRaises(ValueError):
            closet.add_item("ok", "freezing", ("#111111",))
        with self.assertRaises(ValueError):
            closet.add_item("ok", "cold", ("nonsense",))
        self.assertEqual(closet.items(), {})

    def test_rendering_is_deterministic_across_closets(self):
        closet = self.make()
        closet.add_item("coat", "mild", ("#2878b5", "#e8b84b"))
        from mascot_wardrobe.closet import Closet

        other = Closet.init(self.base / "other", self.mascot, self.mask)
        other.add_item("coat", "mild", ("#2878b5", "#e8b84b"))
        self.assertEqual(sha(closet.item_path("coat")), sha(other.item_path("coat")))

    def test_removing_an_item_used_by_a_wardrobe_is_refused(self):
        closet = self.make()
        closet.add_item("coat", "cold", ("#2878b5",))
        closet.save_wardrobe("winter", ["coat"])
        with self.assertRaisesRegex(ValueError, "winter"):
            closet.remove_item("coat")
        self.assertTrue(closet.item_path("coat").exists())


class WardrobeTests(ClosetCase):
    def stocked(self):
        closet = self.make()
        closet.add_item("coat", "very_cold", ("#2878b5", "#e8b84b"))
        closet.add_item("tee", "ambient", ("#aa2222", "#ffffff"))
        closet.add_item("cami", "very_hot", ("#22aa66", "#ffffff"))
        return closet

    def test_save_switch_and_persist_named_wardrobes(self):
        from mascot_wardrobe.closet import Closet

        closet = self.stocked()
        closet.save_wardrobe("winter", ["coat", "tee"])
        closet.save_wardrobe("summer", ["tee", "cami"])
        self.assertEqual(closet.use_wardrobe("summer")["name"], "summer")
        reopened = Closet.open(self.root)
        self.assertEqual(reopened.active, "summer")
        self.assertEqual(sorted(reopened.wardrobes()), ["summer", "winter"])
        self.assertEqual([i["id"] for i in reopened.wardrobes()["winter"]["items"]], ["coat", "tee"])
        reopened.use_wardrobe("winter")
        self.assertEqual(Closet.open(self.root).active, "winter")

    def test_wardrobe_validation(self):
        closet = self.stocked()
        for name, ids in (("../x", ["coat"]), ("ok", []), ("ok", ["ghost"]), ("ok", ["coat", "coat"])):
            with self.subTest(name=name, ids=ids), self.assertRaises(ValueError):
                closet.save_wardrobe(name, ids)
        with self.assertRaises(ValueError):
            closet.use_wardrobe("nope")

    def test_wardrobe_pins_item_hashes_and_goes_stale_when_an_item_is_edited(self):
        closet = self.stocked()
        saved = closet.save_wardrobe("winter", ["coat"])
        self.assertEqual(saved["items"][0]["sha256"], closet.items()["coat"]["sha256"])
        self.assertEqual(saved["items"][0]["version"], 1)
        closet.edit_item("coat", palette=("#000000",))
        with self.assertRaisesRegex(ValueError, "stale|re-save"):
            closet.use_wardrobe("winter")
        with self.assertRaisesRegex(ValueError, "stale|re-save"):
            closet.build_catalog("winter")
        again = closet.save_wardrobe("winter", ["coat"])
        self.assertEqual(again["revision"], 2)
        closet.use_wardrobe("winter")


class CatalogTests(ClosetCase):
    def test_catalog_is_built_from_saved_assets_with_the_same_mascot(self):
        closet = self.make()
        closet.add_item("coat", "very_cold", ("#2878b5", "#e8b84b"))
        closet.add_item("tee", "ambient", ("#aa2222", "#ffffff"))
        closet.save_wardrobe("mix", ["coat", "tee"])
        result = closet.build_catalog("mix")
        sheet = Path(result["sheet"])
        self.assertTrue(sheet.is_file())
        self.assertEqual(result["sheet_sha256"], sha(sheet))
        self.assertEqual(result["mascot_sha256"], sha(self.mascot))
        self.assertEqual([t["item"] for t in result["tiles"]], ["coat", "tee"])
        for tile in result["tiles"]:
            self.assertEqual(tile["sha256"], sha(self.root / tile["asset"]))
            self.assertEqual(tile["mascot_sha256"], sha(self.mascot))
        with Image.open(sheet) as image:
            self.assertEqual(image.size, (tuple(result["sheet_size"])[0], tuple(result["sheet_size"])[1]))
        sidecar = json.loads(Path(result["manifest"]).read_text())
        self.assertEqual(sidecar["tiles"], result["tiles"])

    def test_catalog_pixels_come_from_the_saved_asset_files(self):
        closet = self.make()
        closet.add_item("coat", "cold", ("#2878b5", "#e8b84b"))
        closet.save_wardrobe("one", ["coat"])
        first = closet.build_catalog("one")
        again = closet.build_catalog("one")
        self.assertEqual(first["sheet_sha256"], again["sheet_sha256"])
        closet.edit_item("coat", palette=("#aa2222", "#ffffff"))
        closet.save_wardrobe("one", ["coat"])
        changed = closet.build_catalog("one")
        self.assertNotEqual(first["sheet_sha256"], changed["sheet_sha256"])

    def test_catalog_defaults_to_the_active_wardrobe_and_needs_one(self):
        closet = self.make()
        closet.add_item("coat", "cold", ("#2878b5",))
        with self.assertRaises(ValueError):
            closet.build_catalog()
        closet.save_wardrobe("one", ["coat"])
        closet.use_wardrobe("one")
        self.assertEqual(closet.build_catalog()["wardrobe"], "one")

    def test_catalog_refuses_an_asset_that_alters_the_protected_pixels(self):
        closet = self.make()
        closet.add_item("coat", "cold", ("#2878b5",))
        closet.save_wardrobe("one", ["coat"])
        forged = Image.open(closet.item_path("coat")).convert("RGBA")
        forged.putpixel((1, 1), (255, 0, 0, 255))  # outside the mask
        forged.save(closet.item_path("coat"))
        data = json.loads((self.root / "closet.json").read_text())
        data["items"]["coat"]["sha256"] = sha(closet.item_path("coat"))
        data["wardrobes"]["one"]["items"][0]["sha256"] = data["items"]["coat"]["sha256"]
        (self.root / "closet.json").write_text(json.dumps(data))
        from mascot_wardrobe.closet import Closet

        with self.assertRaisesRegex(ValueError, "protected"):
            Closet.open(self.root)


class MigrationTests(ClosetCase):
    def test_unknown_schema_version_is_rejected(self):
        from mascot_wardrobe.closet import Closet

        self.make()
        path = self.root / "closet.json"
        data = json.loads(path.read_text())
        data["schema_version"] = 99
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "schema"):
            Closet.open(self.root)

    def test_existing_generate_manifest_flow_is_unchanged(self):
        from mascot_wardrobe.application import prepare_wardrobe

        manifest = prepare_wardrobe(
            self.mascot, "pet", "BR", self.base / "catalog", body_box=(0.2, 0.55, 0.8, 0.88)
        )
        self.assertEqual(json.loads(Path(manifest).read_text())["schema_version"], 1)


class LabelTests(ClosetCase):
    PALETTE = ("#2878b5", "#e8b84b")

    def test_name_and_description_default_set_edit_and_persist(self):
        from mascot_wardrobe.closet import Closet

        closet = self.make()
        plain = closet.add_item("rain-coat", "cold", self.PALETTE)
        self.assertEqual((plain["name"], plain["description"]), ("Rain Coat", ""))
        added = closet.add_item("coat", "very_cold", self.PALETTE, name="Warm Coat", description="Wool.")
        self.assertEqual((added["id"], added["name"], added["description"]), ("coat", "Warm Coat", "Wool."))
        closet.edit_item("coat", name="Winter Coat")  # independent: description untouched
        self.assertEqual(closet.items()["coat"]["description"], "Wool.")
        closet.edit_item("coat", description="Wool, double lined.")
        self.assertEqual(closet.items()["coat"]["name"], "Winter Coat")
        closet.edit_item("coat", description="")  # explicit clear
        reopened = Closet.open(self.root)
        self.assertEqual(reopened.items()["coat"]["name"], "Winter Coat")
        self.assertEqual(reopened.items()["coat"]["description"], "")
        stored = json.loads((self.root / "closet.json").read_text())["items"]["coat"]
        self.assertEqual(stored["name"], "Winter Coat")
        self.assertEqual(sorted(reopened.items()), ["coat", "rain-coat"])  # slug ids unchanged

    def test_metadata_only_edit_keeps_pixels_hashes_version_and_wardrobes(self):
        closet = self.make()
        closet.add_item("coat", "very_cold", self.PALETTE)
        closet.save_wardrobe("winter", ["coat"])
        closet.use_wardrobe("winter")
        before = closet.items()["coat"]
        files = {p: sha(p) for p in (closet.source_path, closet.mask_path, closet.item_path("coat"))}
        pinned = closet.wardrobes()
        edited = closet.edit_item("coat", name="Coat!", description="Long wool coat.")
        self.assertEqual((edited["version"], edited["sha256"]), (before["version"], before["sha256"]))
        self.assertEqual(files, {p: sha(p) for p in files})
        self.assertEqual(closet.wardrobes(), pinned)
        closet.verify()
        closet.use_wardrobe("winter")  # not stale
        source = Image.open(self.mascot).convert("RGBA")
        saved = Image.open(closet.item_path("coat")).convert("RGBA")
        outside = Image.open(self.mask).convert("L").point(lambda v: 255 if v == 0 else 0)
        blank = Image.new("RGBA", source.size)
        self.assertEqual(
            Image.composite(saved, blank, outside).tobytes(),
            Image.composite(source, blank, outside).tobytes(),
        )
        with self.assertRaisesRegex(ValueError, "Nothing to change"):
            closet.edit_item("coat", name="Coat!", description="Long wool coat.")

    def test_garment_edit_bumps_version_and_keeps_labels(self):
        closet = self.make()
        closet.add_item("coat", "cold", self.PALETTE, name="My Coat", description="Blue.")
        closet.save_wardrobe("w", ["coat"])
        edited = closet.edit_item("coat", palette=("#aa2222",))
        self.assertEqual((edited["version"], edited["name"], edited["description"]), (2, "My Coat", "Blue."))
        with self.assertRaisesRegex(ValueError, "stale"):
            closet.use_wardrobe("w")

    def test_validation_rejects_instead_of_truncating(self):
        from mascot_wardrobe.closet import MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH

        closet = self.make()
        closet.add_item("coat", "cold", self.PALETTE)
        edge = closet.edit_item("coat", name="n" * MAX_NAME_LENGTH, description="d" * MAX_DESCRIPTION_LENGTH)
        self.assertEqual(len(edge["name"]), MAX_NAME_LENGTH)
        self.assertEqual(len(edge["description"]), MAX_DESCRIPTION_LENGTH)
        bad = [
            {"name": "n" * (MAX_NAME_LENGTH + 1)},
            {"name": "   "},
            {"name": "two\nlines"},
            {"description": "d" * (MAX_DESCRIPTION_LENGTH + 1)},
            {"description": "bell\x07"},
        ]
        for kwargs in bad:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                closet.edit_item("coat", **kwargs)
            with self.subTest(add=kwargs), self.assertRaises(ValueError):
                closet.add_item("other", "cold", self.PALETTE, **kwargs)
        self.assertEqual(closet.items()["coat"]["name"], "n" * MAX_NAME_LENGTH)
        self.assertEqual(sorted(closet.items()), ["coat"])

    def test_legacy_schema_1_records_without_labels_still_open(self):
        from mascot_wardrobe.closet import Closet

        closet = self.make()
        closet.add_item("rain-coat", "cold", self.PALETTE)
        closet.save_wardrobe("w", ["rain-coat"])
        path = self.root / "closet.json"
        data = json.loads(path.read_text())
        del data["items"]["rain-coat"]["name"], data["items"]["rain-coat"]["description"]
        path.write_text(json.dumps(data))
        old = Closet.open(self.root)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(old.items()["rain-coat"]["name"], "Rain Coat")
        self.assertEqual(old.items()["rain-coat"]["description"], "")
        old.use_wardrobe("w")
        tile = old.build_catalog("w")["tiles"][0]
        self.assertEqual((tile["name"], tile["description"]), ("Rain Coat", ""))
        migrated = old.edit_item("rain-coat", description="Now described.")
        self.assertEqual((migrated["version"], migrated["name"]), (1, "Rain Coat"))
        self.assertEqual(Closet.open(self.root).items()["rain-coat"]["description"], "Now described.")

    def corrupt(self, mutate):
        closet = self.make()
        closet.add_item("coat", "cold", self.PALETTE)
        closet.save_wardrobe("w", ["coat"])
        path = self.root / "closet.json"
        data = json.loads(path.read_text())
        mutate(data)
        path.write_text(json.dumps(data))

    def test_malformed_persisted_labels_are_rejected_on_open(self):
        from mascot_wardrobe.closet import MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH, Closet

        def item(**fields):
            return lambda data: data["items"]["coat"].update(fields)

        cases = {
            "null name": item(name=None),
            "int name": item(name=5),
            "list name": item(name=["a"]),
            "empty name": item(name=""),
            "blank name": item(name="   "),
            "long name": item(name="n" * (MAX_NAME_LENGTH + 1)),
            "newline name": item(name="two\nlines"),
            "control name": item(name="bell\x07"),
            "null description": item(description=None),
            "int description": item(description=7),
            "long description": item(description="d" * (MAX_DESCRIPTION_LENGTH + 1)),
            "control description": item(description="tab\x00nul"),
            "item not a dict": lambda data: data["items"].update(coat="oops"),
            "items not a dict": lambda data: data.update(items=[]),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                self.setUp()
                self.corrupt(mutate)
                with self.assertRaises(ValueError):
                    Closet.open(self.root)

    def test_valid_multiline_persisted_description_still_opens(self):
        from mascot_wardrobe.closet import Closet

        self.corrupt(lambda data: data["items"]["coat"].update(description="a\nb\r\nc", name="Ok"))
        self.assertEqual(Closet.open(self.root).items()["coat"]["name"], "Ok")

    def test_unicode_separator_spaces_round_trip_in_names_and_descriptions(self):
        from mascot_wardrobe.closet import Closet

        closet = self.make()
        name, description = "Warm\u00a0Coat\u3000Blue", "wool\u00a0lined\u3000coat\nsecond"
        closet.add_item("coat", "cold", self.PALETTE, name=name, description=description)
        closet.edit_item("coat", description=description + "\u3000x")
        reopened = Closet.open(self.root).items()["coat"]
        self.assertEqual((reopened["name"], reopened["description"]), (name, description + "\u3000x"))

    def test_names_still_reject_control_format_and_line_break_characters(self):
        closet = self.make()
        for bad in ("a\nb", "a\rb", "a\tb", "bell\x07", "a\u200db", "a\u2028b", "a\x85b"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                closet.add_item("coat", "cold", self.PALETTE, name=bad)
        for bad in ("tab\tx", "nul\x00", "a\u200db", "a\u2028b"):
            with self.subTest(description=bad), self.assertRaises(ValueError):
                closet.add_item("coat", "cold", self.PALETTE, description=bad)

    def test_persisted_unicode_spaces_open_and_crlf_is_normalized_without_rewriting(self):
        from mascot_wardrobe.closet import Closet

        self.corrupt(lambda d: d["items"]["coat"].update(name="A\u00a0B\u3000C", description="a\r\nb\rc"))
        path = self.root / "closet.json"
        before = path.read_bytes()
        closet = Closet.open(self.root)
        self.assertEqual(closet.items()["coat"]["name"], "A\u00a0B\u3000C")
        self.assertEqual(closet.items()["coat"]["description"], "a\nb\nc")
        tile = closet.build_catalog("w")["tiles"][0]
        self.assertEqual(tile["description"], "a\nb\nc")
        self.assertEqual(tile["text_lines"][1:], ["a", "b", "c"])
        self.assertEqual(path.read_bytes(), before)

    def test_id_derived_fallback_name_collapses_repeated_hyphens(self):
        from mascot_wardrobe.closet import _fallback_name

        self.assertEqual(_fallback_name("a--b"), "A B")
        self.assertEqual(_fallback_name("rain-coat"), "Rain Coat")
        self.assertEqual(_fallback_name("coat-"), "Coat")


class CatalogLabelTests(ClosetCase):
    LONG = " ".join(f"word{i}" for i in range(60)) + " " + "x" * 300

    def stocked(self, description):
        closet = self.make()
        closet.add_item("coat", "very_cold", ("#2878b5",), name="Winter Coat", description=description)
        closet.add_item("tee", "ambient", ("#aa2222",))
        closet.save_wardrobe("mix", ["coat", "tee"])
        return closet

    def test_tiles_carry_name_and_full_description_below_the_image(self):
        closet = self.stocked(self.LONG[:480])
        result = closet.build_catalog("mix")
        coat, tee = result["tiles"]
        self.assertEqual((coat["name"], tee["name"]), ("Winter Coat", "Tee"))
        self.assertEqual(coat["description"], self.LONG[:480])
        self.assertEqual(json.loads(Path(result["manifest"]).read_text())["tiles"], result["tiles"])
        self.assertGreater(len(coat["text_lines"]), 5)  # wrapped onto several lines
        flat = "".join("".join(line.split()) for line in coat["text_lines"])
        self.assertEqual(flat, "".join(("Winter Coat" + self.LONG[:480]).split()))  # nothing dropped
        for tile in result["tiles"]:
            x, y = tile["position"]
            self.assertGreaterEqual(tile["text_box"][1], y + 1)  # text region starts under the image
            self.assertLessEqual(tile["text_box"][3], result["sheet_size"][1])
        self.assertEqual(coat["text_box"][3], tee["text_box"][3])  # row sized to its tallest label

    def test_text_is_drawn_inside_its_box_and_never_over_the_mascot(self):
        closet = self.stocked(self.LONG[:480])
        plain = self.make_plain_sheet()
        result = closet.build_catalog("mix")
        with Image.open(result["sheet"]) as sheet:
            sheet = sheet.convert("RGB")
            coat = result["tiles"][0]
            left, top, right, bottom = coat["text_box"]
            region = sheet.crop((left, top, right, bottom))
            dark = [p for p in region.tobytes()[::3] if p < 70]
            self.assertGreater(len(dark), 50)  # text pixels exist
            ink_box = region.point(lambda v: 255 if v < 70 else 0).convert("L").getbbox()
            self.assertIsNotNone(ink_box)
            self.assertLessEqual(ink_box[2], right - left)
            self.assertLess(ink_box[3], bottom - top)  # last line is not clipped by the sheet/tile
            # the image area above the text is pixel-identical to a sheet without any label
            image_area = sheet.crop((left, top - 208, right, top))
        self.assertEqual(plain.crop((left, top - 208, right, top)).tobytes(), image_area.tobytes())

    def make_plain_sheet(self):
        """Same tile geometry for the first image area, labels empty and name short."""
        from mascot_wardrobe.closet import Closet

        other = Closet.init(self.base / "plain", self.mascot, self.mask)
        other.add_item("coat", "very_cold", ("#2878b5",), name="W")
        other.add_item("tee", "ambient", ("#aa2222",), name="T")
        other.save_wardrobe("mix", ["coat", "tee"])
        built = other.build_catalog("mix")
        with Image.open(built["sheet"]) as sheet:
            return sheet.convert("RGB").copy()

    def test_metadata_edit_changes_the_sheet_text_but_not_item_assets(self):
        closet = self.stocked("Short.")
        first = closet.build_catalog("mix")
        asset = sha(closet.item_path("coat"))
        closet.edit_item("coat", description="A much longer description. " * 10)
        second = closet.build_catalog("mix")
        self.assertEqual(asset, sha(closet.item_path("coat")))
        self.assertNotEqual(first["sheet_sha256"], second["sheet_sha256"])
        self.assertGreater(second["sheet_size"][1], first["sheet_size"][1])
        self.assertEqual(second["tiles"][0]["sha256"], first["tiles"][0]["sha256"])
        self.assertEqual(second["revision"], first["revision"])

    def test_multiline_and_crlf_descriptions_are_normalized_and_rendered_line_by_line(self):
        closet = self.make()
        closet.add_item("coat", "cold", ("#2878b5",), description="  first\r\nsecond\rthird\n\nlast  ")
        closet.save_wardrobe("w", ["coat"])
        self.assertEqual(closet.items()["coat"]["description"], "first\nsecond\nthird\n\nlast")
        tile = closet.build_catalog("w")["tiles"][0]
        lines = tile["text_lines"]
        self.assertEqual(lines[1:], ["first", "second", "third", "", "last"])  # newlines start new lines
        self.assertFalse(any("\r" in line or "\n" in line for line in lines))

    def test_wrapping_keeps_every_character_and_fits_the_text_width(self):
        from mascot_wardrobe.closet import TEXT_PAD, TILE, _font, _wrap

        font = _font()
        width = TILE[0] - 2 * TEXT_PAD
        text = "short " + "W" * 120 + " tail\n" + "i" * 200 + " ünï cødé ✓"
        lines = _wrap(text, font, width)
        self.assertEqual("".join("".join(line.split()) for line in lines), "".join(text.split()))
        for line in lines:
            self.assertLessEqual(font.getlength(line), width)

    def test_every_tile_text_region_is_inside_the_sheet_below_its_own_portrait(self):
        closet = self.make()
        for index in range(5):  # two rows with different label heights
            closet.add_item(
                f"item-{index}", "cold", ("#2878b5",), description=("word " * (index * 25)).strip()
            )
        closet.save_wardrobe("many", [f"item-{i}" for i in range(5)])
        result = closet.build_catalog("many")
        width, height = result["sheet_size"]
        boxes = []
        with Image.open(result["sheet"]) as sheet:
            self.assertEqual(sheet.size, (width, height))
        for tile in result["tiles"]:
            y = tile["position"][1]
            with Image.open(closet.root / tile["asset"]) as saved:
                portrait = saved.copy()
            portrait.thumbnail((192, 208))
            left, top, right, bottom = tile["text_box"]
            self.assertGreaterEqual(top, y + portrait.height)  # below its own portrait
            self.assertTrue(0 <= left < right <= width and 0 <= top < bottom <= height)
            boxes.append(tile["text_box"])
        for i, a in enumerate(boxes):
            for b in boxes[i + 1 :]:
                separate = a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
                self.assertTrue(separate, (a, b))


class RealMaskFitTests(FitAssertions, unittest.TestCase):
    def test_every_template_fits_the_example_mask_in_the_closet_flow(self):
        from mascot_wardrobe.closet import Closet
        from mascot_wardrobe.wardrobe import CATEGORIES

        mascot_path = ROOT / "examples" / "mascot.png"
        source = Image.open(mascot_path).convert("RGBA")
        for box, head in ((DEFAULT_BOX, False), (EXAMPLE_BODY_BOX, True)):
            with tempfile.TemporaryDirectory() as temporary:
                mask = rect_region(source, pixel_box(box, source.size))
                mask_path = Path(temporary) / "mask.png"
                mask.save(mask_path)
                closet = Closet.init(Path(temporary) / "closet", mascot_path, mask_path)
                bbox = tuple(json.loads((closet.root / "closet.json").read_text())["mask"]["bbox"])
                for index, template in enumerate(CATEGORIES):
                    with self.subTest(box=box, template=template):
                        item_id = template.replace("_", "-")
                        closet.add_item(item_id, template, PROBE)
                        with Image.open(closet.item_path(item_id)) as image:
                            result = image.convert("RGBA")
                        self.assert_fit(source, result, bbox, mask, index, head=head)

    def test_a_template_set_two_closet_is_rejected_with_the_migrate_command(self):
        from mascot_wardrobe.closet import Closet

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            write_mascot(base / "mascot.png")
            write_mask(base / "mask.png")
            Closet.init(base / "closet", base / "mascot.png", base / "mask.png")
            data = json.loads((base / "closet" / "closet.json").read_text())
            data["renderer"] = {"renderer": "local-composer", "templates": 2}
            (base / "closet" / "closet.json").write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "template set 2.*closet --root .* migrate"):
                Closet.open(base / "closet")
            data["renderer"] = {"renderer": "other", "templates": 9}
            (base / "closet" / "closet.json").write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "renderer configuration changed"):
                Closet.open(base / "closet")


class ClosetChoiceTests(ClosetCase):
    def setUp(self):
        super().setUp()
        self.closet = self.make()

    def record(self, item_id="coat"):
        return json.loads((self.root / "closet.json").read_text())["items"][item_id]

    def test_record_stores_style_palette_and_provenance(self):
        added = self.closet.add_item("coat", "cold", ("#2878b5", "#e8b84b"), style="streetwear")
        stored = self.record()
        self.assertEqual(stored["style"], "streetwear")
        self.assertEqual(stored["palette"], ["#2878b5", "#e8b84b"])
        self.assertEqual((stored["style_choice"], stored["palette_choice"]), ("user", "user"))
        self.assertEqual(added["style"], "streetwear")

    def test_library_defaults_stay_classic_and_existing_calls_work(self):
        self.closet.add_item("coat", "cold", ("#2878b5",))
        stored = self.record()
        self.assertEqual(
            (stored["style"], stored["style_choice"], stored["palette_choice"]),
            ("classic", "default", "user"),
        )
        self.closet.add_item("tee", "ambient")
        stored = self.record("tee")
        self.assertEqual((stored["style_choice"], stored["palette_choice"]), ("default", "default"))

    def test_auto_is_deterministic_per_mascot_and_item_id(self):
        first = self.closet.add_item("coat", "cold", "auto", style="auto")
        self.assertEqual((first["style_choice"], first["palette_choice"]), ("auto", "auto"))
        from mascot_wardrobe.closet import Closet
        from mascot_wardrobe.wardrobe.styles import resolve_palette, resolve_style

        seed = sha(self.mascot) + "coat"
        style, _ = resolve_style("auto", "cold", seed)
        palette, _ = resolve_palette("auto", style, "cold", seed)
        self.assertEqual((first["style"], tuple(first["palette"])), (style, palette))
        other = Closet.init(self.base / "other", self.mascot, self.mask)
        again = other.add_item("coat", "cold", "auto", style="auto")
        self.assertEqual(again["sha256"], first["sha256"])

    def test_invalid_values_change_nothing(self):
        before = (self.root / "closet.json").read_bytes()
        for kwargs in ({"style": "grunge"}, {"palette": ("#12",)}, {"palette": ()}, {"palette": "red"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.closet.add_item("coat", "cold", **{"palette": ("#2878b5",), **kwargs})
        self.assertEqual((self.root / "closet.json").read_bytes(), before)
        self.assertFalse((self.root / "items" / "coat.png").exists())

    def test_edit_style_bumps_version_and_rerenders(self):
        added = self.closet.add_item("coat", "cold", ("#2878b5", "#e8b84b"))
        edited = self.closet.edit_item("coat", style="streetwear")
        self.assertEqual(
            (edited["version"], edited["style"], edited["style_choice"]), (2, "streetwear", "user")
        )
        self.assertNotEqual(edited["sha256"], added["sha256"])

    def test_edit_palette_is_a_garment_change(self):
        self.closet.add_item("coat", "cold", ("#2878b5",))
        edited = self.closet.edit_item("coat", palette=("#aa2222",))
        self.assertEqual(edited["version"], 2)

    def test_edit_auto_landing_on_stored_values_changes_only_provenance(self):
        from mascot_wardrobe.wardrobe.styles import resolve_palette, resolve_style

        seed = sha(self.mascot) + "coat"
        style, _ = resolve_style("auto", "cold", seed)
        palette, _ = resolve_palette("auto", style, "cold", seed)
        added = self.closet.add_item("coat", "cold", palette, style=style)
        self.closet.save_wardrobe("w", ["coat"])
        item = self.closet.item_path("coat")
        stamp = item.stat().st_mtime_ns
        edited = self.closet.edit_item("coat", style="auto", palette="auto")
        self.assertEqual((edited["style_choice"], edited["palette_choice"]), ("auto", "auto"))
        self.assertEqual((edited["version"], edited["sha256"]), (1, added["sha256"]))
        self.assertEqual(item.stat().st_mtime_ns, stamp)
        self.assertEqual(self.record()["style_choice"], "auto")
        self.closet.use_wardrobe("w")  # wardrobe still valid
        from mascot_wardrobe.closet import Closet

        Closet.open(self.root)

    def test_fully_identical_edit_is_nothing_to_change(self):
        self.closet.add_item("coat", "cold", ("#2878b5",), style="classic")
        with self.assertRaisesRegex(ValueError, "Nothing to change.*style.*palette"):
            self.closet.edit_item("coat", style="classic", palette=("#2878b5",))
        with self.assertRaisesRegex(ValueError, "Nothing to change"):
            self.closet.edit_item("coat")

    def test_template_change_keeps_stored_style_and_palette(self):
        self.closet.add_item("coat", "cold", "auto", style="auto")
        stored = self.record()
        edited = self.closet.edit_item("coat", template="warm")
        self.assertEqual(edited["version"], 2)
        self.assertEqual((edited["style"], edited["palette"]), (stored["style"], stored["palette"]))
        self.assertEqual((edited["style_choice"], edited["palette_choice"]), ("auto", "auto"))

    def test_explicit_auto_reresolves_with_the_new_template(self):
        self.closet.add_item("coat", "cold", ("#2878b5",), style="classic")
        from mascot_wardrobe.wardrobe.styles import resolve_palette

        edited = self.closet.edit_item("coat", template="warm", palette="auto")
        seed = sha(self.mascot) + "coat"
        self.assertEqual(tuple(edited["palette"]), resolve_palette("auto", "classic", "warm", seed)[0])

    def test_protected_pixels_stay_identical_for_every_style(self):
        source = Image.open(self.mascot).convert("RGBA")
        mask = Image.open(self.mask)
        protected = Image.eval(mask, lambda v: 255 if v == 0 else 0)
        blank = Image.new("RGBA", source.size)
        for style in ("classic", "streetwear"):
            item = self.closet.add_item(
                f"{style}-coat", "cold", ("#101014", "#4920A8", "#9B4DFF", "#DDC5FF"), style=style
            )
            out = Image.open(self.closet.item_path(item["id"])).convert("RGBA")
            self.assertEqual(
                Image.composite(out, blank, protected).tobytes(),
                Image.composite(source, blank, protected).tobytes(),
            )

    def test_items_dir_defaults_and_is_validated(self):
        from mascot_wardrobe.closet import Closet

        self.closet.add_item("coat", "cold", ("#2878b5",))
        path = self.root / "closet.json"
        original = path.read_text()
        for bad in ("../x", str(self.base / "x"), "items/..", "items-tx", "other", "", 5):
            with self.subTest(items_dir=bad):
                data = json.loads(original)
                data["items_dir"] = bad
                path.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, "items_dir"):
                    Closet.open(self.root)

    def test_items_dir_redirects_item_paths(self):
        from mascot_wardrobe.closet import Closet

        self.closet.add_item("coat", "cold", ("#2878b5",))
        self.closet.save_wardrobe("w", ["coat"])
        (self.root / "items-t3").mkdir()
        (self.root / "items-t3" / "coat.png").write_bytes((self.root / "items" / "coat.png").read_bytes())
        path = self.root / "closet.json"
        data = json.loads(path.read_text())
        data["items_dir"] = "items-t3"
        data["items"]["coat"]["file"] = "items-t3/coat.png"
        path.write_text(json.dumps(data))
        closet = Closet.open(self.root)
        self.assertEqual(closet.item_path("coat"), self.root / "items-t3" / "coat.png")
        self.assertEqual(closet.build_catalog("w")["tiles"][0]["asset"], "items-t3/coat.png")
        closet.save_wardrobe("w2", ["coat"])
        with self.assertRaises(ValueError):
            closet.remove_item("coat")
        extra = closet.add_item("hat", "warm", ("#2878b5",))
        self.assertEqual(extra["file"], "items-t3/hat.png")
        self.assertTrue((self.root / "items-t3" / "hat.png").exists())
        closet.remove_item("hat")
        self.assertFalse((self.root / "items-t3" / "hat.png").exists())

    def test_real_mask_fit_flow_for_every_template(self):
        from mascot_wardrobe.closet import Closet
        from mascot_wardrobe.wardrobe import CATEGORIES

        mascot = ROOT / "examples" / "mascot.png"
        source = Image.open(mascot).convert("RGBA")
        mask_path = self.base / "real-mask.png"
        box = pixel_box(EXAMPLE_BODY_BOX, source.size)
        mask = rect_region(source, box)
        mask.save(mask_path)
        closet = Closet.init(self.base / "real", mascot, mask_path)
        for style in ("classic", "streetwear"):
            for template in CATEGORIES:
                item = closet.add_item(f"{style}-{template}".replace("_", "-"), template, PROBE, style=style)
                out = Image.open(closet.item_path(item["id"])).convert("RGBA")
                self.assertNotEqual(out.tobytes(), source.tobytes())


class ClosetMigrateTests(ClosetCase):
    """A closet written in the legacy shape (template set 2, no style fields) and its migration."""

    def setUp(self):
        super().setUp()
        closet = self.make()
        closet.add_item("coat", "cold", ("#2878b5", "#e8b84b"), name="Winter coat", description="Warm.")
        closet.add_item("tee", "ambient", ("#aa2222",))
        closet.edit_item("tee", palette=("#22aa66",))
        # make the files differ from what the current renderer draws, as old-release files would
        data = json.loads((self.root / "closet.json").read_text())
        for item_id, record in data["items"].items():
            path = self.root / "items" / f"{item_id}.png"
            image = Image.open(path).convert("RGBA")
            ImageDraw.Draw(image).rectangle((40, 120, 44, 124), fill=(1, 2, 3, 255))
            image.save(path)
            record["sha256"] = sha(path)
            for key in ("style", "style_choice", "palette_choice"):
                record.pop(key, None)
        data["renderer"] = {"renderer": "local-composer", "templates": 2}
        (self.root / "closet.json").write_text(json.dumps(data, indent=2, sort_keys=True))
        # wardrobe and catalog the legacy way
        data["wardrobes"] = {
            "mix": {
                "revision": 3,
                "items": [
                    {"id": i, "version": data["items"][i]["version"], "sha256": data["items"][i]["sha256"]}
                    for i in ("coat", "tee")
                ],
            }
        }
        data["active"] = "mix"
        self.write(data)
        (self.root / "catalog").mkdir()
        (self.root / "catalog" / "mix.png").write_bytes(b"old sheet")
        (self.root / "catalog" / "mix.json").write_bytes(b"{}")
        self.original = (self.root / "closet.json").read_bytes()
        self.old_files = {
            p.relative_to(self.root): p.read_bytes()
            for sub in ("items", "catalog")
            for p in (self.root / sub).iterdir()
        }

    def write(self, data):
        (self.root / "closet.json").write_text(json.dumps(data, indent=2, sort_keys=True))

    def migrate(self, **kwargs):
        from mascot_wardrobe.closet import Closet

        return Closet.migrate(self.root, **kwargs)

    def test_preserves_identity_and_adds_styles_and_provenance(self):
        from mascot_wardrobe.closet import Closet

        before = json.loads(self.original)
        report = self.migrate()
        self.assertTrue(report["migrated"])
        self.assertEqual(report["stale_catalogs"], ["catalog/mix.png"])
        after = json.loads((self.root / "closet.json").read_text())
        self.assertEqual(after["mask"], before["mask"])
        self.assertEqual(after["mascot"], before["mascot"])
        self.assertEqual(after["items_dir"], "items-t3")
        self.assertEqual(after["active"], "mix")
        closet = Closet.open(self.root)
        for item_id, old in before["items"].items():
            new = after["items"][item_id]
            for key in ("name", "description", "template", "palette", "version"):
                self.assertEqual(new[key], old[key])
            self.assertEqual(
                (new["style"], new["style_choice"], new["palette_choice"]),
                ("classic", "migrated", "migrated"),
            )
            self.assertEqual(new["file"], f"items-t3/{item_id}.png")
            self.assertNotEqual(new["sha256"], old["sha256"])
            self.assertEqual(new["migrated_from"]["sha256"], old["sha256"])
            self.assertEqual(new["migrated_from"]["renderer"], {"renderer": "local-composer", "templates": 2})
            self.assertEqual(closet.item_path(item_id), self.root / "items-t3" / f"{item_id}.png")
        self.assertEqual(after["wardrobes"]["mix"]["revision"], 4)
        self.assertEqual(closet.use_wardrobe("mix")["name"], "mix")
        self.assertEqual(len(closet.build_catalog()["tiles"]), 2)

    def test_backup_is_byte_equal_never_overwritten_and_old_files_untouched(self):
        report = self.migrate()
        backup = self.root / "backups" / "closet-templates-2-1.json"
        self.assertEqual(report["backup"], str(backup))
        self.assertEqual(backup.read_bytes(), self.original)
        now = {
            p.relative_to(self.root): p.read_bytes()
            for sub in ("items", "catalog")
            for p in (self.root / sub).iterdir()
        }
        self.assertEqual(now, {**self.old_files, **{k: v for k, v in now.items() if k not in self.old_files}})
        # a second legacy closet state must get a new backup name
        (self.root / "closet.json").write_bytes(self.original)
        self.migrate()
        self.assertEqual(backup.read_bytes(), self.original)
        self.assertTrue((self.root / "backups" / "closet-templates-2-2.json").exists())

    def test_second_run_is_a_noop(self):
        self.migrate()
        committed = (self.root / "closet.json").read_bytes()
        self.assertEqual(self.migrate(), {"migrated": False, "reason": "already current"})
        self.assertEqual((self.root / "closet.json").read_bytes(), committed)
        self.assertEqual(len(list((self.root / "backups").iterdir())), 1)

    def test_dry_run_writes_nothing(self):
        report = self.migrate(dry_run=True)
        self.assertEqual(report["items"], ["coat", "tee"])
        self.assertEqual(report["wardrobes"], ["mix"])
        self.assertEqual(report["stale_catalogs"], ["catalog/mix.png"])
        self.assertEqual((self.root / "closet.json").read_bytes(), self.original)
        self.assertFalse((self.root / "backups").exists())
        self.assertFalse((self.root / "items-t3").exists())

    def test_render_failure_leaves_closet_json_byte_identical(self):
        from unittest import mock

        from mascot_wardrobe.closet import Closet

        with (
            mock.patch.object(Closet, "_render", side_effect=ValueError("boom")),
            self.assertRaisesRegex(ValueError, "boom"),
        ):
            self.migrate()
        self.assertEqual((self.root / "closet.json").read_bytes(), self.original)

    def test_in_memory_verification_failure_leaves_closet_json_byte_identical(self):
        from unittest import mock

        from mascot_wardrobe.closet import Closet

        real = Closet.verify

        def verify(closet, **kwargs):
            real(closet, **kwargs)  # the legacy check passes; only the new in-memory state fails
            if closet._data.get("items_dir") == "items-t3":
                raise ValueError("bad state")

        with (
            mock.patch.object(Closet, "verify", verify),
            self.assertRaisesRegex(ValueError, "bad state"),
        ):
            self.migrate()
        self.assertEqual((self.root / "closet.json").read_bytes(), self.original)

    def test_post_commit_failure_restores_the_backup(self):
        from unittest import mock

        from mascot_wardrobe.closet import Closet

        with (
            mock.patch.object(Closet, "open", side_effect=ValueError("reopen failed")),
            self.assertRaisesRegex(ValueError, "rolled back.*reopen failed"),
        ):
            self.migrate()
        self.assertEqual((self.root / "closet.json").read_bytes(), self.original)

    def test_leftovers_of_a_failed_attempt_are_overwritten(self):
        (self.root / "items-t3").mkdir()
        (self.root / "items-t3" / "coat.png").write_bytes(b"leftover")
        self.migrate()
        from mascot_wardrobe.closet import Closet

        Closet.open(self.root)

    def test_refusals(self):
        from mascot_wardrobe.closet import Closet

        cases = {}
        cases["tampered item"] = lambda: (self.root / "items" / "coat.png").write_bytes(b"x")
        cases["changed mascot"] = lambda: (self.root / "mascot" / "source.png").write_bytes(b"x")
        cases["changed mask"] = lambda: (self.root / "mask" / "edit-mask.png").write_bytes(b"x")

        def unknown():
            data = json.loads(self.original)
            data["renderer"] = {"renderer": "other", "templates": 9}
            self.write(data)

        def stale_wardrobe():
            data = json.loads(self.original)
            data["wardrobes"]["mix"]["items"][0]["sha256"] = "0" * 64
            self.write(data)

        cases["unknown renderer"] = unknown
        cases["wardrobe mismatch"] = stale_wardrobe
        for label, damage in cases.items():
            with self.subTest(label):
                damage()
                before = (self.root / "closet.json").read_bytes()
                with self.assertRaises(ValueError):
                    Closet.migrate(self.root)
                self.assertEqual((self.root / "closet.json").read_bytes(), before)
                self.assertFalse((self.root / "backups").exists())
                self.setUp()

    def test_already_current_closet_is_reported(self):
        from mascot_wardrobe.closet import Closet

        fresh = Closet.init(self.base / "fresh", self.mascot, self.mask)
        self.assertEqual(Closet.migrate(fresh.root), {"migrated": False, "reason": "already current"})

    def test_opening_a_legacy_closet_points_to_migrate(self):
        from mascot_wardrobe.closet import Closet

        with self.assertRaisesRegex(ValueError, "closet --root .* migrate"):
            Closet.open(self.root)
