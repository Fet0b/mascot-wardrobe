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


class LocalComposer:
    """Overlays geometric templates without modifying the source file."""

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
        layer = Image.new("RGBA", source.size)
        draw = ImageDraw.Draw(layer)

        def rectangle(box, color):
            draw.rectangle(box, fill=color)

        if index == 0:  # closed coat, collar and padded seams
            rectangle((x0, y0, x1 - 1, y1 - 1), primary)
            for line in range(1, 5):
                y = y0 + h * line // 5
                draw.line((x0, y, x1 - 1, y), fill=accent, width=2)
            rectangle((x0 + w // 3, y0, x0 + 2 * w // 3, y0 + h // 4), accent)
        elif index == 1:  # open jacket with collar
            rectangle((x0, y0, x1 - 1, y1 - 1), primary)
            rectangle((x0 + w * 2 // 5, y0, x0 + w * 3 // 5, y1 - 1), accent)
            draw.line((x0, y0, x0 + w // 2, y0 + h // 3, x1 - 1, y0), fill=accent, width=3)
        elif index == 2:  # long-sleeve sweater with a band
            rectangle((x0, y0 + h // 8, x1 - 1, y1 - 1), primary)
            rectangle((x0, y0 + h // 2, x1 - 1, y0 + h * 2 // 3), accent)
        elif index == 3:  # short-sleeve t-shirt
            draw.polygon(
                [
                    (x0, y0),
                    (x1 - 1, y0),
                    (x1 - 1, y0 + h // 3),
                    (x0 + w * 4 // 5, y0 + h // 3),
                    (x0 + w * 4 // 5, y1 - 1),
                    (x0 + w // 5, y1 - 1),
                    (x0 + w // 5, y0 + h // 3),
                    (x0, y0 + h // 3),
                ],
                fill=primary,
            )
            rectangle((x0 + w // 3, y0 + h // 3, x0 + w // 2, y0 + h // 2), accent)
        elif index == 4:  # tank top
            rectangle((x0 + w // 4, y0, x0 + 3 * w // 4, y1 - 1), primary)
            rectangle((x0 + w // 3, y0, x0 + 2 * w // 3, y0 + h // 4), accent)
        else:  # light piece with narrow straps
            rectangle((x0 + w // 4, y0 + h // 2, x0 + 3 * w // 4, y1 - 1), accent)
            for x in (x0 + w // 4, x0 + 3 * w // 4 - max(1, w // 12)):
                rectangle((x, y0, x + max(1, w // 12), y0 + h // 2), primary)
        # Multiplying by the silhouette prevents painting the transparent background.
        body_mask = Image.new("L", source.size)
        ImageDraw.Draw(body_mask).rectangle((x0, y0, x1 - 1, y1 - 1), fill=255)
        alpha = ImageChops.multiply(layer.getchannel("A"), source.getchannel("A"))
        layer.putalpha(ImageChops.multiply(alpha, body_mask))
        return Image.alpha_composite(source, layer)
