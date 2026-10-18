"""Pillow compositor: geometric outfit templates restricted to the body box."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from PIL import Image, ImageChops, ImageColor, ImageDraw

CATEGORIES = ("very_cold", "cold", "mild", "ambient", "warm", "very_hot")


@dataclass(frozen=True)
class OutfitBrief:
    category: str
    palette: tuple[str, ...]
    garments: tuple[str, ...]
    body_box: tuple[float, float, float, float]
    text: str


class Renderer(Protocol):
    """Injectable port: return a real RGBA portrait, never a URL or simulated output.

    A future provider must respect size, transparency and the face region.
    Configurable renderers must expose a JSON-serializable ``cache_config``
    including any version, model or option that affects the pixels. The cache
    binds that configuration and the concrete class; call counters are not options.
    The free-form garments terms are instructions for such a provider; the
    default compositor uses local per-category templates and does not interpret
    natural language.
    """

    def render(self, source: Image.Image, brief: OutfitBrief) -> Image.Image: ...


def _validate_brief(source: Image.Image, brief: OutfitBrief) -> None:
    if not isinstance(source, Image.Image) or source.mode != "RGBA":
        raise ValueError("The compositor requires a loaded RGBA image.")
    if brief.category not in CATEGORIES:
        raise ValueError("Unknown category; exactly six categories are accepted.")
    box = brief.body_box
    if (
        not isinstance(box, (tuple, list))
        or len(box) != 4
        or any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in box)
        or box[0] >= box[2]
        or box[1] >= box[3]
    ):
        raise ValueError("body_box requires four finite, ordered, normalized coordinates.")
    pixel_box = [int(v * n) for v, n in zip(box, source.size * 2, strict=True)]
    if pixel_box[2] - pixel_box[0] < 8 or pixel_box[3] - pixel_box[1] < 8:
        raise ValueError("The body box must be at least 8x8 pixels.")
    if not isinstance(brief.palette, (tuple, list)) or not brief.palette:
        raise ValueError("The palette cannot be empty.")
    try:
        for color in brief.palette:
            if not isinstance(color, str):
                raise ValueError("Invalid color.")
            rgb = ImageColor.getrgb(color)
            if len(rgb) != 3:
                raise ValueError("Use opaque RGB colors.")
    except (TypeError, AttributeError) as exc:
        raise ValueError("Palette contains an invalid color.") from exc


# Template geometry is expressed in fractions (u, v) of the body box, so it scales with any box.
# The torso is a rounded shape slightly inset from the box sides, so the box can include arm
# nubs that sleeves cover and sleeveless tops leave bare. The neckline is a half ellipse
# centred on the top edge: it leaves room for a head that overlaps the box.
_SUPERSAMPLE = 4
_TORSO = (0.115, 0.885)
_TORSO_RADIUS = 0.19  # of the box width, matches a round belly
_SLEEVE_WIDTH = 0.2
_NECK = (0.27, 0.15)  # half width (of w) and depth (of h) of the neckline


class LocalComposer:
    """Overlays fitted geometric templates without modifying the source file.

    The templates fit a body box you provide; they do not detect anatomy.
    ``cache_config`` is part of the cache binding: bump ``templates`` whenever the pixels change.
    """

    cache_config = {"renderer": "local-composer", "templates": 2}

    def render(self, source: Image.Image, brief: OutfitBrief) -> Image.Image:
        _validate_brief(source, brief)
        index = CATEGORIES.index(brief.category)
        primary = ImageColor.getrgb(brief.palette[0])
        accent = ImageColor.getrgb(brief.palette[-1])
        width, height = source.size
        x0, y0, x1, y1 = (
            int(v * n) for v, n in zip(brief.body_box, (width, height, width, height), strict=True)
        )
        w, h = x1 - x0, y1 - y0
        garment, coverage = _garment(index, w, h, primary, accent)
        # Multiplying by the silhouette prevents painting the transparent background. The piece is
        # exactly box-sized, so nothing outside the box can change.
        piece = garment.convert("RGBA")
        piece.putalpha(ImageChops.multiply(coverage, source.crop((x0, y0, x1, y1)).getchannel("A")))
        layer = Image.new("RGBA", source.size)
        layer.paste(piece, (x0, y0))
        return Image.alpha_composite(source, layer)


def _garment(index, w, h, primary, accent):
    """Draw one template at 4x and return its (w x h RGB colors, w x h L coverage)."""
    s = _SUPERSAMPLE
    colors = Image.new("RGB", (w * s, h * s), primary)
    cover = Image.new("L", colors.size)
    paint, shape = ImageDraw.Draw(colors), ImageDraw.Draw(cover)

    def box(u0, v0, u1, v1):
        return (u0 * w * s, v0 * h * s, u1 * w * s - 1, v1 * h * s - 1)

    def mirrored(u0, v0, u1, v1):
        return (box(u0, v0, u1, v1), box(1 - u1, v0, 1 - u0, v1))

    def torso():
        shape.rounded_rectangle(box(_TORSO[0], 0, _TORSO[1], 1), radius=_TORSO_RADIUS * w * s, fill=255)

    def sleeves(end):
        for rect in mirrored(0, 0.04, _SLEEVE_WIDTH, end):
            shape.rounded_rectangle(rect, radius=0.05 * w * s, fill=255)

    def cuffs(end, depth=0.09):
        for rect in mirrored(0, end - depth, _TORSO[0], end):  # only the part outside the torso
            paint.rectangle(rect, fill=accent)

    def neckline(trim=0.0, scale=1.0):
        rx, ry = _NECK[0] * scale, _NECK[1] * scale
        if trim:  # a band hugging the neckline
            band = box(0.5 - rx - trim, -ry - trim * w / h, 0.5 + rx + trim, ry + trim * w / h)
            paint.ellipse(band, fill=accent)
        shape.ellipse(box(0.5 - rx, -ry, 0.5 + rx, ry), fill=0)
        if trim:
            paint.ellipse(box(0.5 - rx, -ry, 0.5 + rx, ry), fill=primary)

    def armholes(depth, reach):
        for rect in mirrored(0.115 - reach, 0.25 - depth, 0.115 + reach, 0.25 + depth):
            shape.ellipse(rect, fill=0)

    def band(v0, v1, color=accent):
        paint.rectangle(box(_TORSO[0], v0, _TORSO[1], v1), fill=color)

    def line(u0, v0, u1, v1, width, color=accent):
        paint.line((u0 * w * s, v0 * h * s, u1 * w * s, v1 * h * s), fill=color, width=round(width * s))

    torso()
    if index == 0:  # padded coat: long sleeves, quilting seams, high collar
        sleeves(0.5)
        for v in (0.3, 0.5, 0.7):
            line(_TORSO[0], v, _TORSO[1], v, 2)
        cuffs(0.5)
        neckline(trim=0.022)
    elif index == 1:  # closed jacket: long sleeves, zipper, collar
        sleeves(0.5)
        line(0.5, 0.12, 0.5, 1, 3)
        band(0.9, 1)
        cuffs(0.5)
        neckline(trim=0.016)
    elif index == 2:  # light cardigan: long sleeves, buttons, ribbed hem
        sleeves(0.5)
        band(0.88, 1)
        cuffs(0.5, 0.06)
        for v in (0.3, 0.5, 0.7):
            paint.ellipse(box(0.49, v - 0.02, 0.51, v + 0.02), fill=accent)
        neckline(trim=0.008)
    elif index == 3:  # t-shirt: short sleeves, crew collar
        sleeves(0.32)
        cuffs(0.32, 0.07)
        neckline(trim=0.012)
    elif index == 4:  # sleeveless tee: armholes, collar
        armholes(0.2, 0.1)
        neckline(trim=0.012)
        band(0.92, 1)
    else:  # camisole: deep scoop, big armholes, light accent body
        paint.rectangle((0, 0, w * s, h * s), fill=accent)
        armholes(0.22, 0.11)
        shape.ellipse(box(0.25, -0.24, 0.75, 0.24), fill=0)
        for rect in mirrored(0.22, 0, 0.29, 0.2):  # thin straps over the shoulders
            shape.rectangle(rect, fill=255)
            paint.rectangle(rect, fill=primary)
        neckline()
        band(0.92, 1, primary)
    size = (w, h)
    return colors.resize(size, Image.Resampling.BOX), cover.resize(size, Image.Resampling.BOX)
