from PIL import Image, ImageDraw

from mascot_wardrobe.wardrobe.composer import LocalComposer, OutfitBrief

PALETTE = ("#101014", "#4920A8", "#9B4DFF", "#DDC5FF")
BODY_BOX = (0.05, 0.28, 0.95, 0.80)


def _synthetic_mascot() -> tuple[Image.Image, Image.Image]:
    """A head-protected torso with two short, horizontal arm silhouettes."""
    source = Image.new("RGBA", (240, 280), (0, 0, 0, 0))
    draw = ImageDraw.Draw(source)
    yellow = (245, 190, 40, 255)
    draw.ellipse((75, 5, 165, 95), fill=yellow)
    draw.rounded_rectangle((77, 84, 163, 220), radius=18, fill=yellow)
    draw.ellipse((25, 105, 99, 145), fill=yellow)
    draw.ellipse((141, 105, 215, 145), fill=yellow)

    # The white edit region covers body/arms only; the head and its outline stay protected.
    region = Image.new("L", source.size, 0)
    mask = ImageDraw.Draw(region)
    mask.rounded_rectangle((82, 97, 158, 212), radius=14, fill=255)
    mask.ellipse((31, 111, 88, 139), fill=255)
    mask.ellipse((152, 111, 209, 139), fill=255)
    return source, region


def _render(category: str) -> tuple[Image.Image, Image.Image]:
    source, region = _synthetic_mascot()
    result = LocalComposer().render(
        source,
        OutfitBrief(category, PALETTE, (), BODY_BOX, "", style="streetwear", region=region),
    )
    return source, result


def _changed(source: Image.Image, result: Image.Image, x: int, y: int) -> bool:
    return source.getpixel((x, y)) != result.getpixel((x, y))


def test_long_sleeves_cover_the_arm_near_the_torso_but_leave_the_hand_tip():
    source, result = _render("very_cold")
    assert _changed(source, result, 76, 122)
    assert not _changed(source, result, 35, 122)


def test_short_sleeves_cover_less_of_the_arm_than_long_sleeves():
    source, long = _render("very_cold")
    _, short = _render("ambient")
    assert _changed(source, short, 76, 122)
    assert not _changed(source, short, 55, 122)
    assert _changed(source, long, 55, 122)


def test_sleeveless_vest_leaves_arms_unpainted():
    source, result = _render("warm")
    assert not _changed(source, result, 76, 122)
    assert not _changed(source, result, 185, 122)


def test_hem_reaches_lower_torso_without_painting_head_or_background():
    source, result = _render("very_cold")
    assert _changed(source, result, 120, 208)
    assert not _changed(source, result, 120, 30)
    assert not _changed(source, result, 10, 160)
