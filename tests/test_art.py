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


def test_pipeline_gives_six_distinct_outfits_and_keeps_pixels_outside_body_box(fresh):
    source_path = ROOT / "examples" / "mascot.png"
    with tempfile.TemporaryDirectory() as out:
        manifest = prepare_wardrobe(source_path, "mascot", "BR", Path(out) / "catalog")
        import json

        outfits = json.loads(manifest.read_text(encoding="utf-8"))["outfits"]
        assert len({o["sha256"]["png"] for o in outfits}) == 6
        with Image.open(source_path) as image:
            source = image.convert("RGBA")
        box = tuple(int(v * n) for v, n in zip(BODY_BOX, source.size * 2, strict=True))
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
