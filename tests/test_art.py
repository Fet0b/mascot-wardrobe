"""Art is reproducible: the committed files equal fresh generator output."""

import hashlib
import importlib.util
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image

from mascot_wardrobe.application import prepare_wardrobe

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "art" / "generate_art.py"
OUTPUTS = ("art/mascot.svg", "examples/mascot.png", "assets/hero.png", "assets/contact-sheet.png")
BODY_BOX = (0.22, 0.60, 0.78, 0.88)
# Explicit box fitted to the example mascot (body plus arm nubs, from the neckline down to the hem).
EXAMPLE_BOX = (0.125, 0.555, 0.875, 0.915)


def _generate(out: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GENERATOR), "--out", str(out), *extra],
        capture_output=True,
        text=True,
        check=False,
        cwd=out,
    )


@pytest.fixture(scope="module")
def fresh():
    with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
        for out in (first, second):
            result = _generate(Path(out))
            assert result.returncode == 0, result.stderr
        yield Path(first), Path(second)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_generator_is_deterministic_for_every_output(fresh):
    first, second = fresh
    for name in OUTPUTS:
        assert (first / name).is_file(), name
        assert _digest(first / name) == _digest(second / name), name


def test_mascot_png_is_rgba_with_transparent_corners_and_opaque_head(fresh):
    with Image.open(fresh[0] / "examples/mascot.png") as image:
        assert image.format == "PNG" and image.mode == "RGBA" and image.size == (512, 512)
        for corner in ((0, 0), (511, 0), (0, 511), (511, 511)):
            assert image.getpixel(corner)[3] == 0
        assert image.getpixel((256, 120))[3] == 255


def test_svg_is_well_formed_with_named_groups(fresh):
    text = (fresh[0] / "art/mascot.svg").read_text(encoding="utf-8")
    assert "<!--" in text and "<image" not in text and "base64" not in text
    root = ET.fromstring(text)
    ids = {element.get("id") for element in root.iter() if element.get("id")}
    assert {"body", "head", "face"} <= ids
    assert root.get("viewBox") == "0 0 512 512"


@pytest.mark.parametrize("body_box", [BODY_BOX, EXAMPLE_BOX])
def test_pipeline_gives_six_distinct_outfits_and_keeps_pixels_outside_body_box(fresh, body_box):
    source_path = ROOT / "examples" / "mascot.png"
    with tempfile.TemporaryDirectory() as out:
        manifest = prepare_wardrobe(source_path, "mascot", "BR", Path(out) / "catalog", body_box=body_box)
        import json

        outfits = json.loads(manifest.read_text(encoding="utf-8"))["outfits"]
        assert len({o["sha256"]["png"] for o in outfits}) == 6
        with Image.open(source_path) as image:
            source = image.convert("RGBA")
        box = tuple(int(v * n) for v, n in zip(body_box, source.size * 2, strict=True))
        for outfit in outfits:
            with Image.open(manifest.parent / outfit["png"]) as image:
                dressed = image.convert("RGBA")
            expected = source.copy()
            expected.paste(dressed.crop(box), box)
            assert expected.tobytes() == dressed.tobytes()
            assert dressed.tobytes() != source.tobytes()


def test_hero_and_contact_sheet_sizes(fresh):
    with Image.open(fresh[0] / "assets/hero.png") as hero:
        assert hero.size == (1600, 500)
    with Image.open(fresh[0] / "assets/contact-sheet.png") as sheet:
        assert sheet.width >= 1200 and sheet.height >= 700


def test_committed_art_equals_fresh_output(fresh):
    assert (ROOT / "art/mascot.svg").read_bytes() == (fresh[0] / "art/mascot.svg").read_bytes()
    with (
        Image.open(ROOT / "examples/mascot.png") as committed,
        Image.open(fresh[0] / "examples/mascot.png") as generated,
    ):
        assert committed.convert("RGBA").tobytes() == generated.convert("RGBA").tobytes()
    for name in ("assets/hero.png", "assets/contact-sheet.png"):
        with Image.open(ROOT / name) as committed, Image.open(fresh[0] / name) as generated:
            assert committed.size == generated.size


def test_check_flag_passes_on_committed_art():
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"], capture_output=True, text=True, check=False, cwd=ROOT
    )
    assert result.returncode == 0, result.stderr


def test_head_and_face_shapes_stay_above_the_default_body_box():
    spec = importlib.util.spec_from_file_location("generate_art", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    top = BODY_BOX[1] * 512
    groups = {ident: shapes for ident, _, shapes in module.GROUPS}
    for ident in ("head", "face", "ears"):
        for shape in groups[ident]:
            if hasattr(shape, "ry"):
                assert shape.cy + shape.ry + module.OUTLINE / 2 <= top


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_art", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_hero_text_fits_left_column_with_a_clear_gap_to_the_mascot():
    boxes = _load_generator().hero_layout()
    column_left, _, column_right, _ = boxes["column"]
    mascot = boxes["mascot"]
    for key in ("title", "tagline", "subline"):
        x0, _, x1, _ = boxes[key]
        assert column_left <= x0 and x1 <= column_right, f"{key} leaves the left column"
        assert mascot[0] - x1 >= 40, f"{key} is closer than 40 px to the mascot"
    assert column_right <= mascot[0]


# --------------------------------------------------------------------------- fitted clothing geometry
CATEGORY_NAMES = ("very_cold", "cold", "mild", "ambient", "warm", "very_hot")


def _group_alpha(module, names):
    """Alpha of the example mascot drawn from the named shape groups only."""
    saved = module.GROUPS
    module.GROUPS = [group for group in saved if group[0] in names]
    try:
        return module.render_mascot(512).getchannel("A")
    finally:
        module.GROUPS = saved


@pytest.fixture(scope="module")
def dressed(fresh):
    """The six real pipeline outfits for the example mascot, with masks of the visible body parts."""
    module = _load_generator()
    source_path = fresh[0] / "examples/mascot.png"
    with tempfile.TemporaryDirectory() as out:
        manifest = prepare_wardrobe(source_path, "mascot", "BR", Path(out) / "catalog", body_box=EXAMPLE_BOX)
        import json

        outfits = json.loads(manifest.read_text(encoding="utf-8"))["outfits"]
        images = {}
        for outfit in outfits:
            with Image.open(manifest.parent / outfit["png"]) as image:
                images[outfit["category"]] = image.convert("RGBA")
    with Image.open(source_path) as image:
        source = image.convert("RGBA")
    head = _group_alpha(module, ("ears", "head", "face"))
    body = _group_alpha(module, ("body",))
    box = tuple(int(v * 512) for v in EXAMPLE_BOX)
    visible = {
        (x, y)
        for y in range(box[1], box[3])
        for x in range(box[0], box[2])
        if body.getpixel((x, y)) == 255 and head.getpixel((x, y)) == 0
    }
    return {"source": source, "images": images, "head": head, "visible": visible, "box": box}


def _changed(data, category):
    source, image = data["source"], data["images"][category]
    return {pos for pos in data["visible"] if source.getpixel(pos) != image.getpixel(pos)}


@pytest.mark.parametrize("category", CATEGORY_NAMES)
def test_clothes_never_touch_head_face_or_ears_even_inside_the_box(dressed, category):
    source, image, head = dressed["source"], dressed["images"][category], dressed["head"]
    x0, y0, x1, y1 = dressed["box"]
    for y in range(y0, y1):
        for x in range(x0, x1):
            if head.getpixel((x, y)):
                assert image.getpixel((x, y)) == source.getpixel((x, y)), (category, x, y)


@pytest.mark.parametrize("category", CATEGORY_NAMES)
def test_every_outfit_wraps_the_whole_torso_to_the_hem(dressed, category):
    x0, y0, x1, y1 = dressed["box"]
    height = y1 - y0
    changed = _changed(dressed, category)
    torso = [
        (x, y)
        for x, y in dressed["visible"]
        if y >= y0 + 0.45 * height and 0.15 <= (x - x0) / (x1 - x0) <= 0.85
    ]
    coverage = sum(pos in changed for pos in torso) / len(torso)
    assert coverage >= 0.9, f"{category} covers only {coverage:.0%} of the lower torso"
    middle_row = [x for x, y in changed if y == y0 + height // 2]
    torso_row = [x for x, y in dressed["visible"] if y == y0 + height // 2 and 100 <= x < 412]  # not the arms
    left_edge, right_edge = min(torso_row), max(torso_row)
    assert min(middle_row) <= left_edge + 12 and max(middle_row) >= right_edge - 12, "side margins are bare"
    assert max(y for x, y in changed if 0.3 <= (x - x0) / (x1 - x0) <= 0.7) >= y1 - 6, "hem stops short"


@pytest.mark.parametrize("category", CATEGORY_NAMES)
def test_every_outfit_meets_the_shoulders(dressed, category):
    x0, _, x1, _ = dressed["box"]
    changed = _changed(dressed, category)
    for lo, hi in ((0.15, 0.4), (0.6, 0.85)):
        side = {(x, y) for x, y in dressed["visible"] if lo <= (x - x0) / (x1 - x0) <= hi}
        top = min(y for _, y in side)
        assert min(y for _, y in side & changed) <= top + 6, f"{category} floats below the shoulders"


def test_sleeves_follow_the_weather(dressed):
    x0, _, x1, _ = dressed["box"]
    arms = {(x, y) for x, y in dressed["visible"] if x < 100 or x >= 412}
    share = {c: len(_changed(dressed, c) & arms) / len(arms) for c in CATEGORY_NAMES}
    for category in ("very_cold", "cold", "mild"):  # long sleeves leave only the hands bare
        assert share[category] >= 0.7, share
    assert 0.2 <= share["ambient"] <= 0.6, share
    for category in ("warm", "very_hot"):
        assert share[category] <= 0.05, share


# --------------------------------------------------------------------------- stale clothing detection
def _slots(module):
    return module.portrait_slots("assets/contact-sheet.png"), module.portrait_slots("assets/hero.png")


def test_check_compares_outfit_thumbnails_but_not_text(fresh):
    module = _load_generator()
    for name in ("assets/contact-sheet.png", "assets/hero.png"):
        with Image.open(fresh[0] / name) as image:
            new = image.convert("RGB")
        assert module.stale_portraits(name, new.copy(), new) == []
        slots = module.portrait_slots(name)
        # A different garment inside one thumbnail is stale art.
        old = new.copy()
        x0, y0, x1, y1 = slots[-1]
        old.paste((200, 30, 30), ((x0 + x1) // 2 - 20, (y0 + y1) // 2, (x0 + x1) // 2 + 20, y1 - 30))
        assert module.stale_portraits(name, old, new) == [len(slots) - 1]
        # Text rasterization may differ between Pillow versions: noise outside the thumbnails is ignored.
        noisy = new.copy()
        noisy.paste((0, 0, 0), (0, 0, 40, 4))
        assert module.stale_portraits(name, noisy, new) == []


def test_committed_thumbnails_match_the_real_pipeline(fresh):
    module = _load_generator()
    for name in ("assets/contact-sheet.png", "assets/hero.png"):
        with Image.open(ROOT / name) as old, Image.open(fresh[0] / name) as new:
            assert module.stale_portraits(name, old.convert("RGB"), new.convert("RGB")) == []


def test_art_pins_the_classic_style_and_the_profile_palette(monkeypatch, tmp_path):
    module = _load_generator()
    seen = {}

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return prepare_wardrobe(*args, **kwargs)

    monkeypatch.setattr(module, "prepare_wardrobe", spy)
    module.build_catalog(ROOT / "examples" / "mascot.png", tmp_path)
    assert seen["style"] == "classic"
    assert seen["palette"] == list(module.load_profile("BR").palette)
