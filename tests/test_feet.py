"""Conservative distal-foot detection and the separate, strictly clipped shoe pass."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image, ImageChops, ImageDraw

from mascot_wardrobe.wardrobe import composer
from mascot_wardrobe.wardrobe.styles import palette_roles

SOURCE = Path(value) if (value := os.environ.get("MASCOT_WARDROBE_FOOT_SOURCE")) else None
PALETTE = ("#101014", "#4920A8", "#9B4DFF", "#DDC5FF")
BOX = (0.22, 0.60, 0.78, 0.88)


def synthetic(kind="feet"):
    image = Image.new("RGBA", (240, 280))
    draw = ImageDraw.Draw(image)
    draw.ellipse((50, 10, 190, 150), fill="#ffe040")
    draw.rectangle((65, 110, 175, 247), fill="#ffe040")
    if kind != "none":
        for x in (65, 135) if kind != "three" else (45, 105, 165):
            draw.ellipse((x, 225, x + 45, 265), fill="#ffe040")
    if kind == "merged":
        draw.rectangle((65, 237, 180, 265), fill="#ffe040")
    return image


def brief(region=None):
    return composer.OutfitBrief("ambient", PALETTE, (), BOX, "shoes", style="streetwear", region=region)


def test_supplied_source_detects_two_feet_and_gets_shoes():
    if SOURCE is None or not SOURCE.exists():
        pytest.skip("Set MASCOT_WARDROBE_FOOT_SOURCE to an external supplied transparent mascot")
    from mascot_wardrobe.wardrobe.feet import detect_feet, shoe_overlay

    source = Image.open(SOURCE).convert("RGBA")
    masks = detect_feet(source.getchannel("A"))
    assert len(masks) == 2
    overlay = shoe_overlay(source.getchannel("A"), palette_roles(PALETTE))
    outline = source.convert("L").point(lambda v: 255 if v < 110 else 0)
    assert ImageChops.multiply(overlay.getchannel("A"), outline).getbbox() is None
    for mask in masks:
        pixels = Image.composite(overlay, Image.new("RGBA", source.size), mask)
        rgba = pixels.tobytes()
        colors = {tuple(rgba[i : i + 3]) for i in range(0, len(rgba), 4) if rgba[i + 3] == 255}
        assert {(16, 16, 20), (73, 32, 168), (221, 197, 255)} <= colors
    assert (
        ImageChops.difference(composer.LocalComposer().render(source, brief()), source)
        .convert("RGB")
        .getbbox()
    )


@pytest.mark.parametrize("kind", ["none", "merged", "three"])
def test_absent_or_ambiguous_feet_add_nothing(kind):
    from mascot_wardrobe.wardrobe.feet import detect_feet, shoe_overlay

    alpha = synthetic(kind).getchannel("A")
    assert detect_feet(alpha) == ()
    assert shoe_overlay(alpha, palette_roles(PALETTE)).getbbox() is None


@pytest.mark.parametrize("scale, offset", [(1, (0, 0)), (2, (27, 19)), (0.75, (41, 7))])
def test_detection_follows_alpha_after_scale_and_translation(scale, offset):
    from mascot_wardrobe.wardrobe.feet import detect_feet

    source = synthetic().resize((round(240 * scale), round(280 * scale)))
    shifted = Image.new("RGBA", (source.width + 80, source.height + 60))
    shifted.paste(source, offset)
    masks = detect_feet(shifted.getchannel("A"))
    assert len(masks) == 2
    assert masks[0].getbbox()[2] < masks[1].getbbox()[0]
    assert all(m.getbbox()[1] > offset[1] + 0.8 * source.height for m in masks)
    assert [m.tobytes() for m in masks] == [m.tobytes() for m in detect_feet(shifted.getchannel("A"))]


@pytest.mark.parametrize("real", [False, True])
def test_only_detected_foot_interiors_change_and_explicit_protection_wins(real):
    from mascot_wardrobe.wardrobe.feet import detect_feet, shoe_overlay

    if real and (SOURCE is None or not SOURCE.exists()):
        pytest.skip("Set MASCOT_WARDROBE_FOOT_SOURCE to an external supplied transparent mascot")
    source = Image.open(SOURCE).convert("RGBA") if real else synthetic()
    alpha = source.getchannel("A")
    masks = detect_feet(alpha)
    assert len(masks) == 2
    allowed = ImageChops.lighter(*masks)
    edit = Image.new("L", source.size)
    ImageDraw.Draw(edit).rectangle((0, int(source.height * 0.60), source.width, source.height), fill=255)
    # Protected hole through a detected foot, plus the protected head above the mask.
    x0, y0, x1, y1 = masks[0].getbbox()
    ImageDraw.Draw(edit).rectangle((x0, y0, (x0 + x1) // 2, y1), fill=0)
    overlay = shoe_overlay(alpha, palette_roles(PALETTE), edit)
    paint = overlay.getchannel("A")
    assert paint.getbbox()
    assert ImageChops.subtract(paint, allowed).getbbox() is None
    assert ImageChops.subtract(paint, edit).getbbox() is None
    assert all(a == 255 for a, p in zip(alpha.tobytes(), paint.tobytes(), strict=True) if p)
    output = composer.LocalComposer().render(source, brief(edit))
    with patch.object(composer, "shoe_overlay", return_value=Image.new("RGBA", source.size)):
        without_shoes = composer.LocalComposer().render(source, brief(edit))
    changed = ImageChops.difference(output, without_shoes).convert("RGB").getbbox()
    assert changed
    before_data = without_shoes.tobytes()
    after_data = output.tobytes()
    allowed_data = allowed.tobytes()
    edit_data = edit.tobytes()
    for pixel in range(source.width * source.height):
        start = pixel * 4
        if not allowed_data[pixel] or not edit_data[pixel]:
            assert before_data[start : start + 4] == after_data[start : start + 4]
    assert output.getchannel("A").tobytes() == alpha.tobytes()
    # Blocking a foot must not change detection or invent another pair from the cut mask.
    assert shoe_overlay(alpha, palette_roles(PALETTE), Image.new("L", source.size)).getbbox() is None


@pytest.mark.parametrize("kind", ["legs", "cropped", "unequal"])
def test_untrustworthy_distal_shapes_are_rejected(kind):
    from mascot_wardrobe.wardrobe.feet import detect_feet

    source = synthetic()
    if kind == "legs":
        source = Image.new("RGBA", (240, 280))
        draw = ImageDraw.Draw(source)
        draw.ellipse((40, 10, 200, 170), fill="yellow")
        draw.rectangle((65, 130, 85, 260), fill="yellow")
        draw.rectangle((155, 130, 175, 260), fill="yellow")
    elif kind == "cropped":
        source = source.crop((0, 0, 240, 259))
    else:
        ImageDraw.Draw(source).rectangle((130, 248, 239, 279), fill=0)
    assert detect_feet(source.getchannel("A")) == ()


def test_generate_accepts_exact_sneakers_but_rejects_extra_outside_box_pixels(tmp_path):
    from mascot_wardrobe.domain.models import Mascot
    from mascot_wardrobe.infrastructure.policies import load_profile
    from mascot_wardrobe.wardrobe import WardrobeAgent
    from mascot_wardrobe.wardrobe.styles import StyleChoice

    source = synthetic()
    path = tmp_path / "source.png"
    source.save(path)
    mascot = Mascot("feet", path, BOX)
    profile = load_profile("BR")
    choice = StyleChoice("streetwear", PALETTE, "user", "user")
    manifest = WardrobeAgent().generate(mascot, profile, tmp_path / "good", choice=choice)
    assert manifest.is_file()

    from mascot_wardrobe.wardrobe.feet import detect_feet

    foot = detect_feet(source.getchannel("A"))[0]
    first = foot.tobytes().index(255)
    foot_pixel = (first % source.width, first // source.width)

    class Damaged(composer.LocalComposer):
        def __init__(self, point):
            self.point = point

        def render(self, source, brief):
            result = super().render(source, brief)
            result.putpixel(self.point, (1, 2, 3, 255))
            return result

    for label, point in (("head", (120, 20)), ("foot", foot_pixel), ("background", (0, 0))):
        output = tmp_path / f"bad-{label}"
        with pytest.raises(ValueError, match="outside the body box"):
            WardrobeAgent(Damaged(point)).generate(mascot, profile, output, choice=choice)
        assert not (output / "manifest.json").exists()
