"""Garment-specific drawing regressions, independent of silhouette differences."""

import itertools

import pytest
from PIL import Image, ImageChops

from mascot_wardrobe.wardrobe import composer
from mascot_wardrobe.wardrobe.fit import Fit
from mascot_wardrobe.wardrobe.styles import palette_roles

ROLES = palette_roles(("#101014", "#4920a8", "#9b4dff", "#ddc5ff"))
WIDTH, HEIGHT = 300, 180


def design(index, offset=0):
    """A measured straight torso: all six designs get exactly the same drawable interior."""
    extents = ((offset + 20, offset + WIDTH - 21),) * HEIGHT
    fit = Fit(HEIGHT, 0, 0, offset + 20, offset + WIDTH - 21, 0, HEIGHT - 1, extents, extents)
    # Keep the box width fixed: only the measured torso moves inside it.
    return composer.compose_layers(
        composer._coverage(index, "streetwear", fit, WIDTH + 40, HEIGHT),
        composer._layers(index, "streetwear", fit, ROLES, WIDTH + 40, HEIGHT),
    ).crop((0, 0, WIDTH + offset, HEIGHT))


def interior_mask(image):
    return Image.eval(
        ImageChops.difference(image.convert("RGB"), Image.new("RGB", image.size, ROLES.base)).convert("L"),
        lambda v: 255 if v else 0,
    ).crop((45, 55, 255, 158))


@pytest.mark.parametrize("index", range(6), ids=composer.CATEGORIES)
def test_black_base_and_restrained_light_details(index):
    image = design(index)
    opaque = [p[:3] for count, p in image.getcolors(WIDTH * HEIGHT) if p[3] == 255 for _ in range(count)]
    assert opaque.count(ROLES.base) / len(opaque) >= 0.65
    assert 0 < opaque.count(ROLES.accent) / len(opaque) < 0.04
    assert ROLES.secondary in opaque
    assert ROLES.highlight in opaque


def test_internal_decoration_is_not_shared_by_all_six_garments():
    masks = [interior_mask(design(i)) for i in range(6)]
    common = masks[0]
    for mask in masks[1:]:
        common = ImageChops.multiply(common, mask)
    assert common.histogram()[255] == 0, "shared shoulder/side seams or chest bars returned"


def test_designs_are_distinct_even_without_silhouette_or_colour_area_differences():
    masks = [interior_mask(design(i)) for i in range(6)]
    for first, second in itertools.combinations(range(6), 2):
        union = ImageChops.lighter(masks[first], masks[second]).histogram()[255]
        differs = ImageChops.difference(masks[first], masks[second]).histogram()[255]
        assert differs / union > 0.45, (composer.CATEGORIES[first], composer.CATEGORIES[second])


@pytest.mark.parametrize("index", range(6), ids=composer.CATEGORIES)
def test_details_follow_the_measured_torso_when_it_moves(index):
    original, shifted = design(index), design(index, offset=19)
    assert original.tobytes() == shifted.crop((19, 0, WIDTH + 19, HEIGHT)).tobytes()


@pytest.mark.parametrize(
    "index, box, required",
    [
        (0, (85, 98, 215, 151), (ROLES.secondary, ROLES.highlight)),  # kangaroo pocket
        (0, (125, 48, 175, 78), (ROLES.accent,)),  # hoodie drawstrings
        (0, (100, 50, 200, 63), (ROLES.secondary,)),  # visible neck warmer below the opening
        (1, (65, 30, 118, 79), (ROLES.secondary, ROLES.highlight)),  # jacket lapel
        (1, (204, 88, 230, 118), (ROLES.accent,)),  # safety pin
        (2, (72, 118, 117, 151), (ROLES.secondary, ROLES.highlight, ROLES.accent)),  # tartan
        (2, (146, 66, 155, 143), (ROLES.accent,)),  # cardigan buttons
        (2, (35, 54, 65, 90), (ROLES.highlight,)),  # compact shoulder stripes even without arm nubs
        (3, (112, 58, 179, 98), (ROLES.secondary, ROLES.highlight)),  # diagonal sling
        (3, (185, 108, 245, 151), (ROLES.secondary, ROLES.accent)),  # bag
        (4, (90, 106, 131, 142), (ROLES.secondary, ROLES.highlight, ROLES.accent)),  # vest tartan
        (5, (174, 121, 222, 157), (ROLES.secondary, ROLES.highlight, ROLES.accent)),  # small pouch
    ],
)
def test_recognizable_garment_details(index, box, required):
    colors = {p[:3] for _count, p in design(index).crop(box).getcolors(WIDTH * HEIGHT) if p[3] == 255}
    assert set(required) <= colors
