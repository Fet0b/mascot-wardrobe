"""Pillow compositor: geometric outfit templates fitted to the measured silhouette.

The compositor owns fit. ``measure_fit`` reads the real silhouette (the source alpha, limited to
the explicit edit mask when one is given), ``_coverage`` shapes the garment on it (neckline,
shoulders, sides, sleeves or armholes, hem), ``_layers`` paints it, and ``compose_layers`` is the
single clip point. Nothing is painted above ``Fit.neck_row`` or outside the body box.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from PIL import Image, ImageChops, ImageColor, ImageDraw

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
    "classic": (1.0, 1.0, 1.0, 0.4, 0.0, 0.0),  # share of the arm nub the sleeve covers
    "streetwear": (1.0, 1.0, 1.0, 0.55, 0.0, 0.0),  # oversized: longer short sleeves
}
SHOULDER_DROP = {"classic": 0.0, "streetwear": 0.1}  # share of the arm nub left bare under the shoulder
ARMHOLES = {  # (depth of h, reach of w) for sleeveless garments
    "classic": {4: (0.2, 0.1), 5: (0.22, 0.11)},
    "streetwear": {4: (0.27, 0.13), 5: (0.29, 0.14)},
}
_TRIM = (0.022, 0.016, 0.008, 0.012, 0.012, 0.0)  # collar band width, of w
_CUFF = (0.09, 0.09, 0.06, 0.07, 0.0, 0.0)  # cuff depth, of h


LEGACY_RENDERER_CONFIG = {"renderer": "local-composer", "templates": 2}  # fixed-fraction templates


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

    A dropped shoulder (streetwear) starts the sleeve lower on the arm nub.
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

    cache_config = {"renderer": "local-composer", "templates": 3, "styles": list(STYLES)}

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
        return Image.alpha_composite(source, layer)


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


def _row_edges(y: int, shape: _Shape, fit: Fit) -> tuple[int, int] | None:
    """Garment columns [left, right) of one row: torso or, on sleeve rows, the whole silhouette."""
    sleeved = shape.sleeve is not None and shape.sleeve[0] <= y <= shape.sleeve[1]
    edge = fit.extents[y] if sleeved else fit.torso[y]
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

    def result(self) -> Image.Image:
        return self.image.resize((self.w, self.h), Image.Resampling.BOX)


def _coverage(index: int, style: str, fit: Fit, w: int, h: int) -> Image.Image:
    """Where the garment exists: torso and sleeves from the measured silhouette, minus the openings."""
    shape = _shape(index, style, fit, w, h)
    canvas = _Canvas(w, h, "L")
    for y in range(fit.neck_row, fit.hem_row + 1):
        edges = _row_edges(y, shape, fit)
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


_STREET_COLLAR = (0.09, 0.075, 0.06, 0.03, 0.03, 0.03)  # hood ring or rib collar width, of w
_STREET_CUFF = (0.07, 0.07, 0.07, 0.04, 0.0, 0.0)  # rib cuff depth, of h


def _streetwear_layers(
    index: int, fit: Fit, roles: PaletteRoles, shape: _Shape, w: int, h: int
) -> list[Image.Image]:
    """Generic geometric streetwear: each template has its own colour-block construction.

    The fabric and the large blocks come first (they carry the palette roles), then the shared
    line details (seams, rib hem and cuffs, hood ring, drawstrings), so lines stay on every
    garment. No text, no logos, no third-party artwork.
    """
    top, span = fit.neck_row, shape.span
    base, secondary, accent, highlight = roles.base, roles.secondary, roles.accent, roles.highlight
    hooded = index <= 2  # very_cold, cold, mild
    fabric = Image.new("RGBA", (w, h), (*(highlight if index == 5 else base), 255))
    detail = _Canvas(w, h, "RGBA")
    torso_w = shape.right - shape.left

    def row(v):
        return top + v * span

    def arm_rows(y0, y1, color):  # the part of the sleeve outside the torso
        for y in range(max(y0, 0), y1 + 1):
            e, t = fit.extents[y], fit.torso[y]
            if e is None:
                continue
            if t is None:
                detail.rect(e[0], y, e[1] + 1, y + 1, color)
            else:
                detail.rect(e[0], y, t[0], y + 1, color)
                detail.rect(t[1] + 1, y, e[1] + 1, y + 1, color)

    def torso_block(v0, v1, color):  # full-width horizontal block
        detail.rect(shape.left, row(v0), shape.right, row(v1), color)

    def sleeve_stripes(color_a, color_b):
        if shape.sleeve is None:
            return
        start, end = shape.sleeve
        length = end - start + 1
        for v, color in ((0.35, color_a), (0.5, color_b)):
            y = start + round(v * length)
            arm_rows(y, y + max(1, round(0.012 * h)) - 1, color)

    # -- colour blocks: one construction per template --------------------------------------
    if index == 0:  # padded parka: contrast sleeves, tall highlight skirt, quilted bands
        if shape.sleeve is not None:
            arm_rows(shape.sleeve[0], shape.sleeve[1], secondary)
        torso_block(0.62, 0.92, highlight)
        for v in (0.3, 0.46):
            detail.line(shape.left, row(v), shape.right, row(v), 2, accent)
        torso_block(0.30, 0.46, secondary)
    elif index == 1:  # zip jacket: racing-stripe side panels from yoke to hem, contrast zip placket
        side = 0.22 * torso_w
        for x0, x1 in ((shape.left, shape.left + side), (shape.right - side, shape.right)):
            detail.rect(x0, row(0.14), x1, row(0.92), secondary)
        detail.rect(
            shape.cx - 0.04 * torso_w, top + shape.dip, shape.cx + 0.04 * torso_w, row(0.92), highlight
        )
        detail.line(shape.cx, top + shape.dip, shape.cx, row(0.56), 3, accent)
        sleeve_stripes(accent, highlight)
    elif index == 2:  # pullover hoodie: deep lower colour block, kangaroo pocket, striped sleeves
        torso_block(0.5, 0.92, secondary)
        x0, x1 = shape.cx - 0.2 * torso_w, shape.cx + 0.2 * torso_w
        y0, y1 = row(0.56), row(0.8)
        detail.rect(x0, y0, x1, y1, accent)
        detail.rect(x0 + 2, y0 + 2, x1 - 2, y1 - 2, base)
        sleeve_stripes(accent, highlight)
    elif index == 3:  # tee: contrast yoke across the shoulders and contrast sleeves
        torso_block(0, 0.36, secondary)
        if shape.sleeve is not None:
            arm_rows(shape.sleeve[0], shape.sleeve[1], secondary)
        torso_block(0.7, 0.92, highlight)
    elif index == 4:  # tank: two-tone split at mid-height with a highlight stripe
        torso_block(0.5, 0.92, secondary)
        torso_block(0.44, 0.5, highlight)
    else:  # camisole: light body, dark hem block, secondary mid band
        torso_block(0.55, 0.92, base)
        torso_block(0.38, 0.46, secondary)
    # -- shared line details ----------------------------------------------------------------
    detail.line(shape.left, row(0.14), shape.right, row(0.14), 2, accent)  # shoulder yoke seam
    for x in (shape.left + 0.18 * torso_w, shape.right - 0.18 * torso_w):  # side seams
        detail.line(x, row(0.14), x, row(0.92), 2, accent)
    # generic chest print: three bars of different length (clear of the yoke block on the tee)
    bar = max(2, round(0.012 * h))
    first_bar = 0.4 if index == 3 else 0.27
    for k, share in enumerate((0.34, 0.24, 0.3)):
        y = row(first_bar) + k * 2.2 * bar
        half = share * torso_w / 2
        detail.rect(shape.cx - half, y, shape.cx + half, y + bar, accent if index == 5 else highlight)
    # rib hem and cuffs
    detail.rect(shape.left, row(0.92), shape.right, fit.hem_row + 1, accent)
    if shape.sleeve is not None:
        end = shape.sleeve[1]
        arm_rows(end - max(1, round(_STREET_CUFF[index] * h)) + 1, end, accent)
    # hood ring (secondary) or rib collar (accent); the coverage keeps both below the neck row
    t = _STREET_COLLAR[index] * w
    detail.ellipse(shape.cx, top, shape.half + t, shape.dip + t, secondary if hooded else accent)
    if hooded:  # drawstrings
        for x in (shape.cx - 0.05 * torso_w, shape.cx + 0.05 * torso_w):
            detail.line(x, top + shape.dip, x, top + shape.dip + 0.1 * span, 2, highlight)
    return [fabric, detail.result()]
