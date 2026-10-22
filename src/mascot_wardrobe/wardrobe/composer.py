"""Pillow compositor: geometric outfit templates fitted to the measured silhouette.

The compositor owns fit. ``measure_fit`` reads the real silhouette (the source alpha, limited to
the explicit edit mask when one is given), ``_coverage`` shapes the garment on it (neckline,
shoulders, sides, sleeves or armholes, hem), ``_layers`` paints it, and ``compose_layers`` is the
single torso clip point. Torso paint stays below ``Fit.neck_row`` inside the body box.
Streetwear sneakers use a separate conservative source-alpha detector and strict interior masks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from PIL import Image, ImageChops, ImageColor, ImageDraw

from mascot_wardrobe.wardrobe.feet import shoe_overlay
from mascot_wardrobe.wardrobe.fit import Fit, measure_fit
from mascot_wardrobe.wardrobe.styles import STYLES, PaletteRoles, roles_from_colors, validate_style

CATEGORIES = ("very_cold", "cold", "mild", "ambient", "warm", "very_hot")


@dataclass(frozen=True)
class OutfitBrief:
    category: str
    palette: tuple[str, ...]
    garments: tuple[str, ...]
    body_box: tuple[float, float, float, float]
    text: str
    style: str = "classic"
    region: Image.Image | None = None  # explicit edit mask (L, source size, 255 paintable)


class Renderer(Protocol):
    """Injectable port: return a real RGBA portrait, never a URL or simulated output.

    A future provider must respect size, transparency and the face region.
    Configurable renderers must expose a JSON-serializable ``cache_config``
    including any version, model or option that affects the pixels. The cache
    binds that configuration and the concrete class; call counters are not options.
    The free-form garments terms are instructions for such a provider; the
    default compositor uses local per-category templates and does not interpret
    natural language. ``style`` names the garment style (see ``wardrobe.styles``); ``region``,
    when given, is the explicit edit mask that limits both the measured silhouette and the paint.
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
    validate_style(brief.style)
    if brief.region is not None and (
        not isinstance(brief.region, Image.Image)
        or brief.region.mode != "L"
        or brief.region.size != source.size
    ):
        raise ValueError("region must be an L image with the size of the source.")


# Fit parameters. Fractions are of the body box height (``h``) or width (``w``); the tests read the
# same numbers through ``side_inset``, ``dip_rows`` and ``sleeve_span``.
_SUPERSAMPLE = 4
NECK_DIP = (0.15, 0.15, 0.15, 0.15, 0.15, 0.24)  # neckline depth below ``fit.neck_row``, per garment
NECK_REACH = 0.34  # the neckline never opens wider than this share of the widest torso row
SIDE_INSET = {"classic": 0.01, "streetwear": 0.0}  # garment edge margin inside the silhouette, of w
SLEEVE_SHARE = {
    "classic": (1.0, 1.0, 1.0, 0.4, 0.0, 0.0),  # legacy vertical sleeve reach
    "streetwear": (0.72, 0.72, 0.72, 0.35, 0.0, 0.0),  # horizontal reach; leave hand tips bare
}
SHOULDER_DROP = {"classic": 0.0, "streetwear": 0.0}  # share of the arm nub left bare under the shoulder
ARMHOLES = {  # (depth of h, reach of w) for sleeveless garments
    "classic": {4: (0.2, 0.1), 5: (0.22, 0.11)},
    "streetwear": {4: (0.27, 0.13), 5: (0.29, 0.14)},
}
_TRIM = (0.022, 0.016, 0.008, 0.012, 0.012, 0.0)  # collar band width, of w
_CUFF = (0.09, 0.09, 0.06, 0.07, 0.0, 0.0)  # cuff depth, of h


LEGACY_RENDERER_CONFIG = {"renderer": "local-composer", "templates": 2}  # fixed-fraction templates
PRIOR_RENDERER_CONFIGS = (
    LEGACY_RENDERER_CONFIG,
    {"renderer": "local-composer", "templates": 3, "styles": list(STYLES)},
    {"renderer": "local-composer", "templates": 4, "styles": list(STYLES)},
)


def side_inset(style: str, w: int) -> int:
    """Pixels between the silhouette edge and the garment edge."""
    return round(SIDE_INSET[style] * w)


def dip_rows(index: int, h: int) -> int:
    """Rows the neckline reaches below ``fit.neck_row``."""
    return round(NECK_DIP[index] * h)


def armhole(style: str, index: int) -> tuple[float, float] | None:
    """(depth of h, reach of w) of the armhole, or None for garments with sleeves."""
    return ARMHOLES[style].get(index)


def sleeve_span(index: int, fit: Fit, style: str = "classic") -> tuple[int, int] | None:
    """First and last row of the sleeve, or None when the garment has no sleeve or no arm nub.

    For streetwear, the horizontal share determines sleeve length; detected rows are all covered.
    Classic templates retain their historical vertical share behavior.
    """
    rows = [
        y
        for y in range(fit.neck_row, fit.hem_row + 1)
        if (e := fit.extents[y]) is not None
        and ((t := fit.torso[y]) is None or t[0] - e[0] >= 2 or e[1] - t[1] >= 2)
    ]
    if not rows or SLEEVE_SHARE[style][index] == 0:
        return None
    top, bottom = rows[0], rows[-1]
    if style == "streetwear":
        return top, bottom
    end = top + max(1, round(SLEEVE_SHARE[style][index] * (bottom - top + 1))) - 1
    return min(top + round(SHOULDER_DROP[style] * (bottom - top + 1)), end), end


def compose_layers(coverage: Image.Image, layers: list[Image.Image], *clips: Image.Image) -> Image.Image:
    """Stack the RGBA layers and clip them: the single place where paint is limited.

    The result alpha is the stack alpha multiplied by ``coverage`` and by every ``clip`` mask, so a
    layer that covers the whole canvas still cannot paint outside the garment.
    """
    piece = Image.new("RGBA", coverage.size)
    for layer in layers:
        piece = Image.alpha_composite(piece, layer)
    alpha = ImageChops.multiply(piece.getchannel("A"), coverage)
    for clip in clips:
        alpha = ImageChops.multiply(alpha, clip)
    piece.putalpha(alpha)
    return piece


class LocalComposer:
    """Overlays fitted geometric templates without modifying the source file.

    The garment is fitted to the silhouette measured inside the body box you provide (and inside
    the edit mask, when given); ``FitError`` is raised when no trustworthy silhouette exists.
    ``cache_config`` is part of the cache binding: bump ``templates`` whenever the pixels change.
    """

    cache_config = {"renderer": "local-composer", "templates": 5, "styles": list(STYLES)}

    def render(self, source: Image.Image, brief: OutfitBrief) -> Image.Image:
        _validate_brief(source, brief)
        index = CATEGORIES.index(brief.category)
        roles = roles_from_colors([ImageColor.getrgb(color)[:3] for color in brief.palette])
        width, height = source.size
        box = tuple(int(v * n) for v, n in zip(brief.body_box, (width, height, width, height), strict=True))
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        alpha = source.crop(box).getchannel("A")
        clips = [alpha]
        region = None
        if brief.region is not None:
            region = brief.region.crop(box)
            clips.append(region)
        fit = measure_fit(alpha, region)
        coverage = _coverage(index, brief.style, fit, w, h)
        layers = _layers(index, brief.style, fit, roles, w, h)
        # The piece is exactly box-sized, so nothing outside the box can change; the clips keep it
        # off the transparent background and outside the edit mask.
        layer = Image.new("RGBA", source.size)
        layer.paste(compose_layers(coverage, layers, *clips), (x0, y0))
        result = Image.alpha_composite(source, layer)
        if brief.style == "streetwear":
            result = Image.alpha_composite(result, shoe_overlay(source.getchannel("A"), roles, brief.region))
        return result


@dataclass(frozen=True)
class _Shape:
    inset: int
    cx: float  # neck centre
    half: float  # neckline half width
    dip: int
    span: int  # rows from the neck row to the hem row, inclusive
    left: int  # outermost torso columns over the body rows
    right: int  # exclusive
    sleeve: tuple[int, int] | None


def _shape(index: int, style: str, fit: Fit, w: int, h: int) -> _Shape:
    body = [fit.torso[y] for y in range(fit.neck_row, fit.hem_row + 1) if fit.torso[y] is not None]
    widest = max(b - a + 1 for a, b in body)
    span = fit.hem_row + 1 - fit.neck_row
    return _Shape(
        inset=side_inset(style, w),
        cx=(fit.neck_left + fit.neck_right + 1) / 2,
        half=min((fit.neck_right - fit.neck_left + 1) / 2, NECK_REACH * widest),
        dip=min(dip_rows(index, h), (span - 1) // 2),
        span=span,
        left=min(a for a, _ in body),
        right=max(b for _, b in body) + 1,
        sleeve=sleeve_span(index, fit, style),
    )


def _row_edges(y: int, shape: _Shape, fit: Fit, index: int, style: str) -> tuple[int, int] | None:
    """Garment columns [left, right) for the torso and fitted sleeve reach."""
    sleeved = shape.sleeve is not None and shape.sleeve[0] <= y <= shape.sleeve[1]
    torso = fit.torso[y]
    if sleeved and style == "streetwear":
        extent = fit.extents[y]
        if extent is None or torso is None:
            return None
        share = SLEEVE_SHARE[style][index]
        left_reach = max(0, torso[0] - extent[0])
        right_reach = max(0, extent[1] - torso[1])
        left = torso[0] - round(left_reach * share)
        right = torso[1] + round(right_reach * share) + 1
    else:
        edge = fit.extents[y] if sleeved else torso
        if edge is None:
            return None
        left, right = edge[0], edge[1] + 1
    if right - left > 2 * shape.inset:  # tiny rows at the hem keep their full width
        left, right = left + shape.inset, right - shape.inset
    return left, right


class _Canvas:
    """Supersampled drawing surface addressed in box pixels."""

    def __init__(self, w: int, h: int, mode: str):
        self.w, self.h = w, h
        self.image = Image.new(mode, (w * _SUPERSAMPLE, h * _SUPERSAMPLE))
        self.draw = ImageDraw.Draw(self.image)

    def rect(self, x0, y0, x1, y1, fill) -> None:
        if x1 > x0 and y1 > y0:
            s = _SUPERSAMPLE
            self.draw.rectangle((x0 * s, y0 * s, x1 * s - 1, y1 * s - 1), fill=fill)

    def ellipse(self, cx, cy, rx, ry, fill) -> None:
        s = _SUPERSAMPLE
        self.draw.ellipse(((cx - rx) * s, (cy - ry) * s, (cx + rx) * s - 1, (cy + ry) * s - 1), fill=fill)

    def line(self, x0, y0, x1, y1, width, fill) -> None:
        s = _SUPERSAMPLE
        self.draw.line((x0 * s, y0 * s, x1 * s, y1 * s), fill=fill, width=max(1, round(width * s)))

    def polygon(self, points, fill) -> None:
        self.draw.polygon([(x * _SUPERSAMPLE, y * _SUPERSAMPLE) for x, y in points], fill=fill)

    def round_rect(self, x0, y0, x1, y1, radius, fill) -> None:
        if x1 > x0 and y1 > y0:
            s = _SUPERSAMPLE
            self.draw.rounded_rectangle(
                (x0 * s, y0 * s, x1 * s - 1, y1 * s - 1), radius=radius * s, fill=fill
            )

    def result(self) -> Image.Image:
        return self.image.resize((self.w, self.h), Image.Resampling.BOX)


def _coverage(index: int, style: str, fit: Fit, w: int, h: int) -> Image.Image:
    """Where the garment exists: torso and sleeves from the measured silhouette, minus the openings."""
    shape = _shape(index, style, fit, w, h)
    canvas = _Canvas(w, h, "L")
    for y in range(fit.neck_row, fit.hem_row + 1):
        edges = _row_edges(y, shape, fit, index, style)
        if edges is not None:
            canvas.rect(edges[0], y, edges[1], y + 1, 255)
    if (opening := armhole(style, index)) is not None:
        depth, reach = opening
        ry = round(depth * h / 2)
        top = fit.shoulder_row + 1  # the armhole starts right under the shoulder row
        centre = min(top + ry, fit.hem_row)
        edge = fit.torso[centre] or fit.torso[fit.shoulder_row] or (shape.left, shape.right - 1)
        for x in (edge[0], edge[1] + 1):
            canvas.ellipse(x, top + ry, reach * w, ry, 0)
    canvas.ellipse(shape.cx, fit.neck_row, shape.half, shape.dip, 0)  # nothing above neck_row exists
    return canvas.result()


def _layers(index: int, style: str, fit: Fit, roles: PaletteRoles, w: int, h: int) -> list[Image.Image]:
    """Ordered RGBA paint layers; ``compose_layers`` clips them to the coverage."""
    shape = _shape(index, style, fit, w, h)
    if style == "streetwear":
        return _streetwear_layers(index, fit, roles, shape, w, h)
    top, span = fit.neck_row, shape.span
    base, accent = roles.base, roles.accent

    def row(v):  # a row at share v of the garment height
        return top + v * span

    fabric = Image.new("RGBA", (w, h), (*(accent if index == 5 else base), 255))
    detail = _Canvas(w, h, "RGBA")

    def band(v0, v1, color=accent):
        detail.rect(shape.left, row(v0), shape.right, fit.hem_row + 1 if v1 >= 1 else row(v1), color)

    def cuffs(depth):
        if shape.sleeve is None:
            return
        end = shape.sleeve[1]
        for y in range(end - max(1, round(depth * h)) + 1, end + 1):
            e, t = fit.extents[y], fit.torso[y]
            if e is None:
                continue
            if t is None:
                detail.rect(e[0], y, e[1] + 1, y + 1, accent)
            else:
                detail.rect(e[0], y, t[0], y + 1, accent)  # only the part outside the torso
                detail.rect(t[1] + 1, y, e[1] + 1, y + 1, accent)

    def collar(trim):
        if trim:  # a band hugging the neckline
            t = trim * w
            detail.ellipse(shape.cx, top, shape.half + t, shape.dip + t, accent)

    def line(v0, v1, x0, x1, width):
        detail.line(x0, row(v0), x1, row(v1), width, accent)

    if index == 0:  # padded coat: long sleeves, quilting seams, high collar
        for v in (0.3, 0.5, 0.7):
            line(v, v, shape.left, shape.right, 2)
        cuffs(_CUFF[0])
    elif index == 1:  # closed jacket: long sleeves, zipper, collar
        line(0.12, 1, shape.cx, shape.cx, 3)
        band(0.9, 1)
        cuffs(_CUFF[1])
    elif index == 2:  # light cardigan: long sleeves, buttons, ribbed hem
        band(0.88, 1)
        cuffs(_CUFF[2])
        for v in (0.3, 0.5, 0.7):
            detail.ellipse(shape.cx, row(v), 0.01 * w, 0.01 * w, accent)
    elif index == 3:  # t-shirt: short sleeves, crew collar
        cuffs(_CUFF[3])
    elif index == 4:  # sleeveless tee: armholes, collar
        band(0.92, 1)
    else:  # camisole: deep scoop, big armholes, light accent body, thin straps
        strap = 0.07 * w
        for x0 in (shape.cx - shape.half - strap, shape.cx + shape.half):
            detail.rect(x0, top, x0 + strap, row(0.2), base)
        band(0.92, 1, base)
    collar(_TRIM[index])
    return [fabric, detail.result()]


def _streetwear_layers(
    index: int, fit: Fit, roles: PaletteRoles, shape: _Shape, w: int, h: int
) -> list[Image.Image]:
    """Six compact, unbranded garments, drawn relative to the measured torso.

    The base remains dominant. Secondary panels and highlight piping describe each garment;
    the accent role supplies sparse hardware. All shapes use the existing coverage/clip path.
    """
    base, secondary, accent, highlight = roles.base, roles.secondary, roles.accent, roles.highlight
    fabric = Image.new("RGBA", (w, h), (*base, 255))
    detail = _Canvas(w, h, "RGBA")
    torso_w = shape.right - shape.left
    # Scale strokes with the available torso, but keep small hardware legible in tiny portraits.
    stroke = max(1.5, min(torso_w * 0.006, shape.span * 0.012))

    def x(v):
        return shape.left + v * torso_w

    def y(v):
        return fit.neck_row + v * shape.span

    def line(u0, v0, u1, v1, color=highlight, width=stroke):
        detail.line(x(u0), y(v0), x(u1), y(v1), width, color)

    def panel(points, color):
        detail.polygon([(x(u), y(v)) for u, v in points], color)

    def pocket(u0, v0, u1, v1, trim=highlight):
        radius = min((u1 - u0) * torso_w, (v1 - v0) * shape.span) * 0.18
        detail.round_rect(x(u0), y(v0), x(u1), y(v1), radius, trim)
        detail.round_rect(
            x(u0) + stroke,
            y(v0) + stroke,
            x(u1) - stroke,
            y(v1) - stroke,
            max(0, radius - stroke),
            secondary,
        )

    def stud(u, v):
        detail.ellipse(x(u), y(v), stroke, stroke, accent)

    def tartan(u0, v0, u1, v1):
        # A single bounded swatch, with sparse crossing threads rather than a large colour block.
        detail.rect(x(u0), y(v0), x(u1), y(v1), secondary)
        for share in (0.25, 0.65):
            u, v = u0 + share * (u1 - u0), v0 + share * (v1 - v0)
            line(u, v0, u, v1, highlight)
            line(u0, v, u1, v, highlight)
        line(u0, v0 + 0.45 * (v1 - v0), u1, v0 + 0.45 * (v1 - v0), accent)

    def collar(trim, color):
        detail.ellipse(
            shape.cx,
            fit.neck_row,
            shape.half + trim * torso_w,
            shape.dip + trim * shape.span,
            color,
        )

    def lapels(depth, reach):
        for mirrored in (False, True):
            points = [(0.22, 0.09), (0.46, 0.18), (0.36, depth), (reach, 0.26)]
            panel([(1 - u if mirrored else u, v) for u, v in points], secondary)
            direction = -1 if mirrored else 1
            start = 1 if mirrored else 0
            line(start + direction * 0.22, 0.09, start + direction * 0.36, depth)

    if index == 0:  # hoodie, low neck warmer and angular kangaroo pocket
        collar(0.035, secondary)
        detail.round_rect(x(0.29), y(0.10), x(0.71), y(0.36), 0.035 * torso_w, secondary)
        line(0.33, 0.345, 0.67, 0.345)
        for u, end in ((0.43, 0.40), (0.57, 0.60)):
            line(u, 0.36, end, 0.48, accent)
        outline = [(0.31, 0.54), (0.69, 0.54), (0.77, 0.65), (0.74, 0.83), (0.26, 0.83), (0.23, 0.65)]
        panel(outline, highlight)
        panel([(0.32, 0.56), (0.68, 0.56), (0.75, 0.66), (0.72, 0.81), (0.28, 0.81), (0.25, 0.66)], secondary)
        line(0.31, 0.58, 0.27, 0.70, base, 2 * stroke)
        line(0.69, 0.58, 0.73, 0.70, base, 2 * stroke)
    elif index == 1:  # punk jacket: folded lapels, off-centre zip, three studs and a safety pin
        lapels(0.43, 0.16)
        line(0.52, 0.29, 0.46, 0.94)
        stud(0.50, 0.52)
        for u, v in ((0.26, 0.18), (0.31, 0.22), (0.74, 0.18)):
            stud(u, v)
        pocket(0.18, 0.67, 0.35, 0.79, secondary)
        line(0.20, 0.69, 0.32, 0.69)
        # Closed, angled pin with a clasp, clear of the zip and lapels.
        for a, b in [
            ((0.73, 0.49), (0.78, 0.57)),
            ((0.78, 0.57), (0.75, 0.63)),
            ((0.75, 0.63), (0.70, 0.55)),
            ((0.70, 0.55), (0.73, 0.49)),
        ]:
            line(*a, *b, accent)
        stud(0.73, 0.49)
    elif index == 2:  # emo cardigan: V opening, tiny buttons, sleeve stripes, one tartan patch
        line(0.30, 0.05, 0.50, 0.34, secondary, 3 * stroke)
        line(0.70, 0.05, 0.50, 0.34, secondary, 3 * stroke)
        line(0.50, 0.34, 0.50, 0.93, secondary, 3 * stroke)
        for v in (0.40, 0.57, 0.74):
            stud(0.50, v)
        # Continue the restrained stripe motif onto the shoulder when a compact box excludes arms.
        for v in (0.30, 0.39, 0.48):
            line(0.04, v, 0.14, v)
        if shape.sleeve is not None:
            start, end = shape.sleeve
            for share in (0.40, 0.60, 0.80):
                row = start + round(share * (end - start))
                # Stripes end at the measured torso; they never become chest bars.
                for row_y in range(row, min(end + 1, row + max(1, round(stroke)))):
                    e, t = fit.extents[row_y], fit.torso[row_y]
                    if e is not None and t is not None:
                        detail.rect(e[0], row_y, t[0], row_y + 1, highlight)
                        detail.rect(t[1] + 1, row_y, e[1] + 1, row_y + 1, highlight)
        tartan(0.20, 0.66, 0.37, 0.84)
    elif index == 3:  # oversized tee with one diagonal strap and a compact sling bag
        collar(0.012, highlight)
        line(0.24, 0.18, 0.77, 0.70, secondary, 5 * stroke)
        line(0.25, 0.18, 0.78, 0.70, highlight)
        pocket(0.63, 0.58, 0.87, 0.85, secondary)
        line(0.66, 0.64, 0.84, 0.64)
        stud(0.81, 0.65)
    elif index == 4:  # punk vest: short lapels, studs and one small tartan pocket
        lapels(0.34, 0.22)
        line(0.50, 0.36, 0.50, 0.93, secondary, 2 * stroke)
        tartan(0.27, 0.59, 0.43, 0.79)
        for u, v in ((0.64, 0.46), (0.70, 0.46), (0.76, 0.46)):
            stud(u, v)
        line(0.67, 0.61, 0.73, 0.68)
        line(0.73, 0.68, 0.68, 0.77)
    else:  # sleeveless scoop tank with a short pouch strap, binding and a small belt pouch
        collar(0.012, secondary)
        line(0.69, 0.43, 0.72, 0.69, secondary, 3 * stroke)
        line(0.70, 0.43, 0.73, 0.69)
        pocket(0.59, 0.67, 0.79, 0.88)
        line(0.62, 0.72, 0.76, 0.72, accent)
    return [fabric, detail.result()]
