"""Optional stored fit box: independent from the edit mask, validated, used by every render."""

import json
import math
import unittest

from PIL import Image

from mascot_wardrobe.closet import Closet
from mascot_wardrobe.wardrobe import LocalComposer, OutfitBrief
from tests.test_cli_closet import ClosetCLIBase
from tests.test_closet import ClosetCase, sha

BODY_BOX = (0.06, 0.62, 0.94, 0.93)
PALETTE = ("#2878b5", "#e8b84b")


def direct_render(closet, box, template="cold", style="classic"):
    source = Image.open(closet.source_path).convert("RGBA")
    mask = Image.open(closet.mask_path).convert("L")
    brief = OutfitBrief(template, PALETTE, (), box, "closet item", style=style, region=mask)
    dressed = LocalComposer().render(source.copy(), brief)
    return Image.composite(dressed, source, mask).tobytes()


def item_pixels(closet, item_id):
    return Image.open(closet.item_path(item_id)).convert("RGBA").tobytes()


class BodyBoxTests(ClosetCase):
    def make_boxed(self, box=BODY_BOX, root=None):
        return Closet.init(root or self.root, self.mascot, self.mask, body_box=box)

    def test_body_box_is_persisted_and_survives_reopen(self):
        self.make_boxed()
        data = json.loads((self.root / "closet.json").read_text())
        self.assertEqual(data["body_box"], list(BODY_BOX))
        Closet.open(self.root)

    def test_omitted_body_box_leaves_no_json_field(self):
        self.make()
        self.assertNotIn("body_box", json.loads((self.root / "closet.json").read_text()))

    def test_render_uses_the_stored_box_not_the_mask_bbox(self):
        closet = self.make_boxed()
        closet.add_item("coat", "cold", PALETTE, style="classic")
        self.assertEqual(item_pixels(closet, "coat"), direct_render(closet, BODY_BOX))
        legacy = Closet.init(self.base / "legacy", self.mascot, self.mask)
        legacy.add_item("coat", "cold", PALETTE, style="classic")
        self.assertNotEqual(sha(closet.item_path("coat")), sha(legacy.item_path("coat")))

    def test_edit_rerenders_with_the_stored_box(self):
        closet = self.make_boxed()
        closet.add_item("coat", "cold", PALETTE, style="classic")
        closet.edit_item("coat", palette=("#aa2222", "#ffffff"))
        self.assertEqual(Closet.open(self.root).items()["coat"]["version"], 2)

    def test_legacy_closet_still_uses_the_mask_bbox(self):
        closet = self.make()
        closet.add_item("coat", "cold", PALETTE, style="classic")
        x0, y0, x1, y1 = closet._data["mask"]["bbox"]
        w, h = Image.open(closet.source_path).size
        box = tuple(min(1.0, (v + 0.5) / n) for v, n in zip((x0, y0, x1, y1), (w, h, w, h), strict=True))
        self.assertEqual(item_pixels(closet, "coat"), direct_render(closet, box))

    def test_invalid_body_boxes_are_rejected_at_init(self):
        bad = (
            (0.1, 0.1, 0.5),
            (0.1, 0.1, 0.5, 0.6, 0.7),
            (0.5, 0.1, 0.1, 0.6),
            (0.1, 0.6, 0.5, 0.6),
            (-0.1, 0.1, 0.5, 0.6),
            (0.1, 0.1, 1.5, 0.6),
            (0.1, 0.1, math.nan, 0.6),
            (0.1, 0.1, math.inf, 0.6),
            (0.1, 0.1, 0.5, True),
            ("0.1", 0.1, 0.5, 0.6),
            "box",
        )
        for index, box in enumerate(bad):
            with self.subTest(box=box), self.assertRaises(ValueError):
                self.make_boxed(box, root=self.base / f"bad-{index}")
            self.assertFalse((self.base / f"bad-{index}" / "closet.json").exists())

    def test_invalid_stored_body_box_is_rejected_on_open(self):
        self.make_boxed()
        path = self.root / "closet.json"
        for stored in ([0.5, 0.1, 0.1, 0.6], [0.1, 0.1, 0.5], "x", None, [0.1, 0.1, 2, 0.6]):
            data = json.loads(path.read_text())
            data["body_box"] = stored
            path.write_text(json.dumps(data))
            with self.subTest(stored=stored), self.assertRaises(ValueError):
                Closet.open(self.root)


class BodyBoxCLITests(ClosetCLIBase):
    def init_args(self, *box):
        return ("init", "--mascot", self.base / "m.png", "--mask", self.base / "mask.png", *box)

    def test_init_accepts_body_box_and_persists_it(self):
        self.closet_cmd(*self.init_args("--body-box", "0.06", "0.62", "0.94", "0.93"))
        data = json.loads((self.closet / "closet.json").read_text())
        self.assertEqual(data["body_box"], [0.06, 0.62, 0.94, 0.93])

    def test_init_rejects_an_unordered_body_box(self):
        result = self.closet_cmd(*self.init_args("--body-box", "0.9", "0.6", "0.1", "0.9"), ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.closet / "closet.json").exists())


if __name__ == "__main__":
    unittest.main()
