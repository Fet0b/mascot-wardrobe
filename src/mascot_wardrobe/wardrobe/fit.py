"""Silhouette measurement: where the garment may start, where the shoulders and the hem are.

The compositor owns fit. It measures the real silhouette (the source alpha, intersected with an
explicit edit mask when one exists) and fails clearly when no trustworthy silhouette exists.
There is no fallback to fixed fractions of the body box.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from PIL import Image, ImageChops

_THRESHOLD = 127
_MIN_ROWS = 8
_MIN_PIXELS = 64
_NECK_BAND = 0.35  # of the box height: the neck is searched in the top band
_NECK_LOOKAHEAD = 0.2  # of the box height, below the narrowest row
_NECK_CONTRAST = 1.15  # the head side must be at least this much wider than the neck row
_SHOULDER_SHARE = 0.9  # of the widest torso row

Extent = tuple[int, int]


class FitError(ValueError):
    """The silhouette cannot be measured, so the garment cannot be fitted."""


@dataclass(frozen=True)
class Fit:
    height: int
    top_row: int  # first row (box-relative) with silhouette pixels
    neck_row: int  # first row where the garment may start; nothing above it is painted
    neck_left: int
    neck_right: int  # silhouette extents at the neck row
    shoulder_row: int
    hem_row: int  # last row with silhouette pixels
    extents: tuple[Extent | None, ...]  # per box-relative row: (left, right) of the silhouette
    torso: tuple[Extent | None, ...]  # per row: extents with arm nubs removed


def _mask(image: Image.Image, name: str) -> Image.Image:
    if not isinstance(image, Image.Image):
        raise FitError(f"The {name} must be a loaded image.")
    return image.convert("L").point(lambda v: 255 if v > _THRESHOLD else 0)


def _extents(silhouette: Image.Image) -> tuple[Extent | None, ...]:
    width, height = silhouette.size
    data = silhouette.tobytes()
    rows: list[Extent | None] = []
    for y in range(height):
        row = data[y * width : (y + 1) * width]
        left = row.find(255)
        rows.append(None if left < 0 else (left, row.rfind(255)))
    return tuple(rows)


def measure_fit(alpha: Image.Image, region: Image.Image | None = None) -> Fit:
    """Measure the silhouette of ``alpha`` (cropped to the body box), limited to ``region`` if given."""
    silhouette = _mask(alpha, "alpha")
    if region is not None:
        mask = _mask(region, "region")
        if mask.size != silhouette.size:
            raise FitError("The alpha and the edit mask must have the same size.")
        silhouette = ImageChops.multiply(silhouette, mask)
    width, height = silhouette.size
    pixels = silhouette.histogram()[255]
    extents = _extents(silhouette)
    filled = [y for y, e in enumerate(extents) if e is not None]
    if len(filled) < _MIN_ROWS or pixels < _MIN_PIXELS:
        raise FitError(
            "No body silhouette in the body box: move the body box over the mascot's body "
            "or use a PNG with a transparent background."
        )
    if region is None and pixels == width * height:
        raise FitError(
            "The body box is fully opaque, so the silhouette cannot be measured: "
            "use a transparent PNG or a closet edit mask."
        )

    top_row, hem_row = filled[0], filled[-1]
    widths = {y: extents[y][1] - extents[y][0] + 1 for y in filled}

    band = [y for y in filled if y <= int(_NECK_BAND * height)]
    narrowest = min(band, key=lambda y: (widths[y], y))
    ahead = min(narrowest + int(_NECK_LOOKAHEAD * height), hem_row)
    ahead = max((y for y in filled if y <= ahead), default=narrowest)
    neck_row = top_row
    if (
        narrowest > top_row
        and widths[top_row] >= _NECK_CONTRAST * widths[narrowest]
        and widths[ahead] >= _NECK_CONTRAST * widths[narrowest]
    ):
        neck_row = narrowest
    neck_left, neck_right = extents[neck_row]

    # Belly window: median width of the lower half, centred on the median centre of those rows.
    lower = [y for y in filled if y >= height // 2] or filled
    belly = median(widths[y] for y in lower)
    centre = median((extents[y][0] + extents[y][1]) / 2 for y in lower)
    window = (round(centre - belly / 2), round(centre + belly / 2) - 1)
    torso: list[Extent | None] = []
    for e in extents:
        if e is None:
            torso.append(None)
            continue
        left, right = max(e[0], window[0]), min(e[1], window[1])
        torso.append((left, right) if left <= right else None)

    body = [y for y in filled if y >= neck_row and torso[y] is not None]
    if not body:
        raise FitError("The silhouette has no torso below the neck: check the body box.")
    torso_width = {y: torso[y][1] - torso[y][0] + 1 for y in body}
    widest = max(torso_width.values())
    shoulder_row = next(y for y in body if torso_width[y] >= _SHOULDER_SHARE * widest)
    if neck_right - neck_left + 1 > widest or shoulder_row > hem_row or neck_row >= hem_row:
        raise FitError(
            "The measured silhouette is inconsistent (the neck is wider than the torso): "
            "adjust the body box so it starts at the neck or below the head."
        )
    return Fit(
        height=height,
        top_row=top_row,
        neck_row=neck_row,
        neck_left=neck_left,
        neck_right=neck_right,
        shoulder_row=shoulder_row,
        hem_row=hem_row,
        extents=extents,
        torso=tuple(torso),
    )
