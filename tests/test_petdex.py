"""Hermes atlas generation uses only temporary fixtures and Pillow."""

import hashlib
import io
from unittest import mock

import pytest
from PIL import Image, ImageDraw

from mascot_wardrobe.wardrobe import petdex

ROWS = (
    "idle",
    "running-right",
    "running-left",
    "waving",
    "jumping",
    "failed",
    "waiting",
    "running",
    "review",
)


def source_png(*, opaque=False):
    image = Image.new("RGBA", (160, 200), (238, 238, 238, 255) if opaque else (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((25, 15, 135, 185), fill=(80, 100, 180, 255))
    draw.ellipse((45, 30, 115, 90), fill=(255, 255, 255, 255))  # enclosed face details
    draw.ellipse((60, 44, 67, 51), fill="black")
    draw.rectangle((45, 105, 115, 160), fill=(200, 60, 50, 255))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def test_exact_source_deterministic_atlas_contract_and_png_round_trip():
    png = source_png()
    before = hashlib.sha256(png).hexdigest()
    plan = petdex.build_atlas(png)
    assert plan == petdex.build_atlas(png)
    assert plan.source_sha256 == before
    assert plan.sha256 == hashlib.sha256(plan.png).hexdigest()
    assert plan.slug == "closet-pet-" + plan.sha256
    assert plan.rows == ROWS
    assert not plan.background_removed
    with Image.open(io.BytesIO(plan.png)) as sheet:
        assert (sheet.format, sheet.mode, sheet.size) == ("PNG", "RGBA", (1536, 1872))
        for row in range(9):
            frames = []
            for col in range(8):
                frame = sheet.crop((col * 192, row * 208, (col + 1) * 192, (row + 1) * 208))
                box = frame.getchannel("A").getbbox()
                if col >= 6:
                    assert box is None
                else:
                    assert box is not None
                    assert 8 <= box[0] < box[2] <= 184
                    assert 8 <= box[1] < box[3] <= 200
                    frames.append(frame.tobytes())
            assert len(set(frames)) == 6, ROWS[row]
        buffer = io.BytesIO()
        sheet.save(buffer, "PNG")
        with Image.open(io.BytesIO(buffer.getvalue())) as decoded:
            assert decoded.tobytes() == sheet.tobytes()
    assert hashlib.sha256(png).hexdigest() == before


def test_edge_connected_removal_preserves_enclosed_white_and_source_bytes():
    png = source_png(opaque=True)
    item, removed = petdex.prepare_source(png)
    assert removed is True
    assert item.getpixel((0, 0))[3] == 0
    assert item.getpixel((80, 65)) == (255, 255, 255, 255)
    assert item.getpixel((80, 130)) == (200, 60, 50, 255)
    assert png == source_png(opaque=True)
    assert petdex.build_atlas(png).background_removed


def test_transparent_source_preserves_every_rgba_pixel():
    png = source_png()
    with Image.open(io.BytesIO(png)) as original:
        prepared, removed = petdex.prepare_source(png)
        assert prepared.tobytes() == original.tobytes()
        assert not removed


@pytest.mark.parametrize("kind", ["textured", "dark", "touching-edge", "blank", "empty", "invalid"])
def test_ambiguous_background_or_invalid_source_fails_closed(kind):
    image = Image.open(io.BytesIO(source_png(opaque=True))).convert("RGBA")
    if kind == "textured":
        image.putpixel((0, 0), (220, 220, 220, 255))
    elif kind == "dark":
        image = Image.new("RGBA", image.size, (60, 60, 60, 255))
    elif kind == "touching-edge":
        ImageDraw.Draw(image).rectangle((0, 70, 50, 90), fill="blue")
    elif kind in ("blank", "empty"):
        image = Image.new("RGBA", image.size, (238, 238, 238, 255) if kind == "blank" else 0)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    with pytest.raises(ValueError, match="background|visible|PNG"):
        petdex.build_atlas(b"invalid" if kind == "invalid" else buffer.getvalue())


def test_validator_refuses_bad_geometry_taxonomy_duplicates_unused_columns_and_clipping():
    plan = petdex.build_atlas(source_png())
    with pytest.raises(ValueError, match="taxonomy"):
        petdex.validate_atlas(plan.png, rows=tuple(reversed(ROWS)))
    for problem in ("geometry", "duplicate", "unused", "clipping"):
        with Image.open(io.BytesIO(plan.png)) as original:
            sheet = original.copy()
        if problem == "geometry":
            sheet = sheet.crop((0, 0, 100, 100))
        elif problem == "duplicate":
            sheet.paste(sheet.crop((0, 0, 192, 208)), (192, 0))
        elif problem == "unused":
            sheet.putpixel((7 * 192, 0), (0, 0, 0, 255))
        else:
            sheet.putpixel((0, 0), (0, 0, 0, 255))
        buffer = io.BytesIO()
        sheet.save(buffer, "PNG")
        with pytest.raises(ValueError, match="geometry|different|unused|margin"):
            petdex.validate_atlas(buffer.getvalue())


def test_build_is_in_memory_and_does_not_import_cairosvg_or_live_store():
    with (
        mock.patch("pathlib.Path.mkdir", side_effect=AssertionError("mkdir forbidden")),
        mock.patch("pathlib.Path.write_bytes", side_effect=AssertionError("write forbidden")),
        mock.patch("tempfile.TemporaryDirectory", side_effect=AssertionError("staging forbidden")),
        mock.patch.dict("sys.modules", {"cairosvg": None, "agent.pet.store": None}),
    ):
        petdex.build_atlas(source_png())


def test_white_background_removal_does_not_remove_enclosed_matching_white_face():
    image = Image.open(io.BytesIO(source_png(opaque=True))).convert("RGBA")
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            if pixels[x, y] == (238, 238, 238, 255):
                pixels[x, y] = (255, 255, 255, 255)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    prepared, removed = petdex.prepare_source(buffer.getvalue())
    assert removed
    assert prepared.getpixel((0, 0))[3] == 0
    assert prepared.getpixel((80, 65)) == (255, 255, 255, 255)
