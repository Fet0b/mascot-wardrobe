"""Reproducible generator for the Mascot Wardrobe artwork.

Original work by the Mascot Wardrobe contributors, MIT licensed. Pillow only, no network.

The creature is described once, as a list of shapes in a 512x512 design space.
From that single description this script emits:

* ``art/mascot.svg``           hand-editable vector source (named groups, comments)
* ``examples/mascot.png``      512x512 transparent PNG (supersampled)
* ``assets/hero.png``          1600x500 README banner
* ``assets/contact-sheet.png`` six outfits from the real ``prepare_wardrobe`` pipeline

Usage::

    python art/generate_art.py            # write the files into the repository
    python art/generate_art.py --out DIR  # write the same layout under DIR
    python art/generate_art.py --check    # exit 1 if committed art is out of date
"""

from __future__ import annotations

import argparse
import math
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFont, ImageStat

REPO = Path(__file__).resolve().parent.parent
try:
    from mascot_wardrobe.application import prepare_wardrobe
    from mascot_wardrobe.infrastructure.policies import load_profile
except ImportError:  # running from a plain checkout without installing the package
    sys.path.insert(0, str(REPO / "src"))
    from mascot_wardrobe.application import prepare_wardrobe
    from mascot_wardrobe.infrastructure.policies import load_profile

CREAM, MINT, CORAL, INK = "#FFF4DC", "#BFE8D5", "#FF8A7A", "#33415C"
CREAM_DEEP, MINT_DEEP, WHITE, MUTED = "#F6E2B8", "#8FD0B3", "#FFFFFF", "#5B6B88"
OUTLINE = 8  # stroke width in design units
SUPERSAMPLE = 4
DESIGN = 512
# Explicit body box for this mascot, shared by the demo images, the quickstart and the e2e script:
# arm nubs (x 64..448 of 512), from just under the head to the hem inside the body outline.
EXAMPLE_BODY_BOX = (0.125, 0.555, 0.875, 0.915)
STALE_THUMBNAIL_TOLERANCE = 1.5  # mean 0..255 difference per thumbnail; text drift stays far below
OUTPUT_NAMES = ("art/mascot.svg", "examples/mascot.png", "assets/hero.png", "assets/contact-sheet.png")


# --------------------------------------------------------------------------- shapes
@dataclass(frozen=True)
class Ellipse:
    cx: float
    cy: float
    rx: float
    ry: float
    fill: str
    rot: float = 0.0
    stroke: bool = False
    opacity: float = 1.0

    def grown(self, delta: float) -> Ellipse:
        return Ellipse(self.cx, self.cy, self.rx + delta, self.ry + delta, self.fill, self.rot)

    def points(self, scale: float) -> list[tuple[float, float]]:
        cos, sin = math.cos(math.radians(self.rot)), math.sin(math.radians(self.rot))
        out = []
        for i in range(240):
            t = 2 * math.pi * i / 240
            x, y = self.rx * math.cos(t), self.ry * math.sin(t)
            out.append(((self.cx + x * cos - y * sin) * scale, (self.cy + x * sin + y * cos) * scale))
        return out

    def draw(self, draw: ImageDraw.ImageDraw, scale: float, color) -> None:
        draw.polygon(self.points(scale), fill=color)

    def svg(self) -> str:
        rot = f' transform="rotate({num(self.rot)} {num(self.cx)} {num(self.cy)})"' if self.rot else ""
        return (
            f'<ellipse cx="{num(self.cx)}" cy="{num(self.cy)}" rx="{num(self.rx)}" ry="{num(self.ry)}"'
            f"{rot}{paint(self)}/>"
        )


@dataclass(frozen=True)
class RRect:
    x: float
    y: float
    w: float
    h: float
    r: float
    fill: str
    stroke: bool = False
    opacity: float = 1.0

    def grown(self, delta: float) -> RRect:
        return RRect(
            self.x - delta, self.y - delta, self.w + 2 * delta, self.h + 2 * delta, self.r + delta, self.fill
        )

    def draw(self, draw: ImageDraw.ImageDraw, scale: float, color) -> None:
        draw.rounded_rectangle(
            (self.x * scale, self.y * scale, (self.x + self.w) * scale, (self.y + self.h) * scale),
            radius=self.r * scale,
            fill=color,
        )

    def svg(self) -> str:
        return (
            f'<rect x="{num(self.x)}" y="{num(self.y)}" width="{num(self.w)}" height="{num(self.h)}" '
            f'rx="{num(self.r)}"{paint(self)}/>'
        )


@dataclass(frozen=True)
class Curve:
    """Quadratic Bezier stroke with round caps (mouth, sprout stem)."""

    p0: tuple[float, float]
    c: tuple[float, float]
    p1: tuple[float, float]
    color: str
    width: float

    def draw(self, draw: ImageDraw.ImageDraw, scale: float, color) -> None:
        radius = self.width / 2 * scale
        for i in range(121):
            t = i / 120
            x = (1 - t) ** 2 * self.p0[0] + 2 * (1 - t) * t * self.c[0] + t * t * self.p1[0]
            y = (1 - t) ** 2 * self.p0[1] + 2 * (1 - t) * t * self.c[1] + t * t * self.p1[1]
            draw.ellipse(
                (x * scale - radius, y * scale - radius, x * scale + radius, y * scale + radius), fill=color
            )

    def svg(self) -> str:
        (x0, y0), (cx, cy), (x1, y1) = self.p0, self.c, self.p1
        return (
            f'<path d="M {num(x0)} {num(y0)} Q {num(cx)} {num(cy)} {num(x1)} {num(y1)}" fill="none" '
            f'stroke="{self.color}" stroke-width="{num(self.width)}" stroke-linecap="round"/>'
        )

    fill = property(lambda self: self.color)
    stroke = False
    opacity = 1.0


def num(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def paint(shape) -> str:
    attrs = f' fill="{shape.fill}"'
    if shape.opacity != 1.0:
        attrs += f' fill-opacity="{num(shape.opacity)}"'
    if shape.stroke:
        attrs += f' stroke="{INK}" stroke-width="{OUTLINE}"'
    return attrs


# Each group: (svg id, comment, shapes). Painting order is list order, back to front.
# The head ends above y=307. The default body box starts there; EXAMPLE_BODY_BOX starts higher and
# relies on the compositor neckline, which leaves the lowest part of the head untouched.
GROUPS = [
    ("shadow", "Soft ground shadow under the feet.", [Ellipse(256, 494, 150, 12, INK, opacity=0.14)]),
    (
        "feet",
        "Two rounded feet, drawn behind the body.",
        [
            Ellipse(188, 470, 48, 26, CREAM, stroke=True),
            Ellipse(324, 470, 48, 26, CREAM, stroke=True),
        ],
    ),
    (
        "body",
        "Round body with arm nubs. Outfits are fitted to the box (0.125,0.555)-(0.875,0.915) of the PNG.",
        [
            Ellipse(98, 352, 28, 40, CREAM, rot=14, stroke=True),
            Ellipse(414, 352, 28, 40, CREAM, rot=-14, stroke=True),
            RRect(104, 284, 304, 188, 78, CREAM, stroke=True),
            Ellipse(256, 392, 92, 60, WHITE, opacity=0.35),
        ],
    ),
    (
        "ears",
        "Ears: cream outside, mint inside, behind the head.",
        [
            Ellipse(122, 92, 48, 62, CREAM, rot=-24, stroke=True),
            Ellipse(390, 92, 48, 62, CREAM, rot=24, stroke=True),
            Ellipse(125, 98, 24, 38, MINT, rot=-24),
            Ellipse(387, 98, 24, 38, MINT, rot=24),
        ],
    ),
    (
        "head",
        "Large friendly head with a mint sprout. Its lowest point stays above the body box.",
        [
            Curve((256, 54), (256, 30), (266, 20), INK, 7),
            Ellipse(284, 24, 22, 11, MINT, rot=-28, stroke=True),
            Ellipse(232, 28, 18, 9, MINT_DEEP, rot=26, stroke=True),
            Ellipse(256, 171, 178, 130, CREAM, stroke=True),
            Ellipse(170, 108, 52, 22, WHITE, rot=-24, opacity=0.5),
        ],
    ),
    (
        "face",
        "Eyes, cheeks, nose and smile, all well above the body box.",
        [
            Ellipse(190, 176, 19, 25, INK),
            Ellipse(322, 176, 19, 25, INK),
            Ellipse(197, 166, 7, 8, WHITE),
            Ellipse(329, 166, 7, 8, WHITE),
            Ellipse(138, 212, 30, 19, CORAL, opacity=0.55),
            Ellipse(374, 212, 30, 19, CORAL, opacity=0.55),
            Ellipse(256, 205, 10, 7, CORAL),
            Curve((232, 222), (256, 246), (280, 222), INK, 7),
        ],
    ),
]


def build_svg() -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!-- Mascot Wardrobe mascot. Original work by the Mascot Wardrobe contributors, MIT license. -->",
        "<!-- Generated by art/generate_art.py from one shape list; edit the script to change both. -->",
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512" '
        'role="img" aria-labelledby="title">',
        '  <title id="title">Mascot Wardrobe mascot: a small round cream creature with mint ears</title>',
        f"  <!-- Palette: cream {CREAM}, mint {MINT}, coral {CORAL}, ink {INK} -->",
    ]
    for ident, comment, shapes in GROUPS:
        lines.append(f"  <!-- {comment} -->")
        lines.append(f'  <g id="{ident}">')
        lines.extend(f"    {shape.svg()}" for shape in shapes)
        lines.append("  </g>")
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def render_mascot(size: int) -> Image.Image:
    """Render the shape list at ``size`` pixels square with 4x supersampling."""
    scale = size / DESIGN * SUPERSAMPLE
    big = round(size * SUPERSAMPLE)
    canvas = Image.new("RGBA", (big, big))
    for _, _, shapes in GROUPS:
        for shape in shapes:
            if isinstance(shape, Curve):
                layers = [(shape, shape.color)]
            elif shape.stroke:
                layers = [(shape.grown(OUTLINE / 2), INK), (shape.grown(-OUTLINE / 2), shape.fill)]
            else:
                layers = [(shape, shape.fill)]
            for item, color in layers:
                layer = Image.new("RGBA", canvas.size)
                rgb = ImageColor.getrgb(color)
                alpha = round(255 * shape.opacity)
                item.draw(ImageDraw.Draw(layer), scale, (*rgb, alpha))
                canvas = Image.alpha_composite(canvas, layer)
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


# --------------------------------------------------------------------------- composition helpers
def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def gradient(size: tuple[int, int], top: str, bottom: str) -> Image.Image:
    width, height = size
    a, b = ImageColor.getrgb(top), ImageColor.getrgb(bottom)
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / (height - 1)
        draw.line((0, y, width, y), fill=tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3)))
    return image.convert("RGBA")


def card(size: tuple[int, int], radius: int, fill=(255, 255, 255, 190)) -> Image.Image:
    scale = 3
    big = Image.new("RGBA", (size[0] * scale, size[1] * scale))
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, big.width - 1, big.height - 1), radius=radius * scale, fill=fill
    )
    return big.resize(size, Image.Resampling.LANCZOS)


def centered(draw: ImageDraw.ImageDraw, cx: float, y: float, text: str, face, fill) -> None:
    draw.text((cx, y), text, font=face, fill=fill, anchor="ms")


def build_catalog(mascot_png: Path, workdir: Path) -> list[dict]:
    """Run the real pipeline (BR preset) and return outfits with image, label and range."""
    import json

    manifest = prepare_wardrobe(mascot_png, "mascot", "BR", workdir / "catalog", body_box=EXAMPLE_BODY_BOX)
    outfits = json.loads(manifest.read_text(encoding="utf-8"))["outfits"]
    profile = load_profile("BR")
    bounds = profile.thresholds_c
    ranges = (
        [f"below {bounds[0]:g} °C"]
        + [f"{lo:g} to {hi:g} °C" for lo, hi in zip(bounds, bounds[1:], strict=False)]
        + [f"{bounds[-1]:g} °C and above"]
    )
    result = []
    for outfit, text, garment in zip(outfits, ranges, profile.garments, strict=True):
        with Image.open(manifest.parent / outfit["png"]) as image:
            portrait = image.convert("RGBA")
        label = outfit["category"].replace("_", " ").capitalize()
        result.append({"image": portrait, "label": label, "range": text, "garment": garment})
    return result


SHEET_PAD, SHEET_CARD, SHEET_COLS, SHEET_PORTRAIT = 40, (400, 410), 3, 290
HERO_PICKS = (0, 3, 5)  # very cold, ambient, very hot
HERO_THUMB = 150


def sheet_card(index: int) -> tuple[int, int]:
    col, row = index % SHEET_COLS, index // SHEET_COLS
    return (
        SHEET_PAD + col * (SHEET_CARD[0] + SHEET_PAD),
        70 + SHEET_PAD + row * (SHEET_CARD[1] + SHEET_PAD),
    )


def hero_center(slot: int) -> int:
    return 1070 + 85 + slot * 150


def portrait_slots(name: str) -> list[tuple[int, int, int, int]]:
    """Output-pixel boxes of the outfit thumbnails in a composite image (no text inside them)."""
    if name == "assets/contact-sheet.png":
        boxes = []
        for index in range(6):
            x, y = sheet_card(index)
            left, top = x + (SHEET_CARD[0] - SHEET_PORTRAIT) // 2, y + 14
            boxes.append((left, top, left + SHEET_PORTRAIT, top + SHEET_PORTRAIT))
        return boxes
    if name == "assets/hero.png":
        return [
            (hero_center(slot) - HERO_THUMB // 2, 200, hero_center(slot) + HERO_THUMB // 2, 200 + HERO_THUMB)
            for slot in range(len(HERO_PICKS))
        ]
    raise ValueError(name)


def stale_portraits(name: str, old: Image.Image, new: Image.Image) -> list[int]:
    """Indices of outfit thumbnails whose pixels differ beyond rasterization noise.

    Text depends on the Pillow wheel, but the thumbnails come from the pipeline and must match.
    """
    stale = []
    for index, box in enumerate(portrait_slots(name)):
        diff = ImageChops.difference(old.convert("RGB").crop(box), new.convert("RGB").crop(box))
        if max(ImageStat.Stat(diff).mean) > STALE_THUMBNAIL_TOLERANCE:
            stale.append(index)
    return stale


def build_contact_sheet(outfits: list[dict]) -> Image.Image:
    pad, (cw, ch), cols = SHEET_PAD, SHEET_CARD, SHEET_COLS
    width = cols * cw + (cols + 1) * pad
    height = 2 * ch + 3 * pad + 70
    sheet = gradient((width, height), "#E6F6EE", "#FFF4DC")
    draw = ImageDraw.Draw(sheet)
    centered(draw, width / 2, 58, "Six outfits, one mascot", font(34), INK)
    for index, outfit in enumerate(outfits):
        x, y = sheet_card(index)
        sheet.alpha_composite(card((cw, ch), 28), (x, y))
        portrait = outfit["image"].resize((SHEET_PORTRAIT, SHEET_PORTRAIT), Image.Resampling.LANCZOS)
        sheet.alpha_composite(portrait, (x + (cw - SHEET_PORTRAIT) // 2, y + 14))
        centered(draw, x + cw / 2, y + 340, outfit["label"], font(30), INK)
        centered(draw, x + cw / 2, y + 370, f"{outfit['range']}  ·  {outfit['garment']}", font(20), MUTED)
    return sheet.convert("RGB")


def sun(draw: ImageDraw.ImageDraw, cx: float, cy: float) -> None:
    draw.ellipse((cx - 17, cy - 17, cx + 17, cy + 17), fill=CORAL)
    for i in range(8):
        a = math.radians(i * 45)
        draw.line(
            (cx + 26 * math.cos(a), cy + 26 * math.sin(a), cx + 36 * math.cos(a), cy + 36 * math.sin(a)),
            fill=CORAL,
            width=5,
        )


def cloud(draw: ImageDraw.ImageDraw, cx: float, cy: float) -> None:
    for dx, dy, r in ((-16, 4, 13), (0, -6, 18), (17, 4, 14)):
        draw.ellipse((cx + dx - r, cy + dy - r, cx + dx + r, cy + dy + r), fill=MINT_DEEP)
    draw.rounded_rectangle((cx - 28, cy, cx + 31, cy + 17), radius=8, fill=MINT_DEEP)


def snowflake(draw: ImageDraw.ImageDraw, cx: float, cy: float) -> None:
    for i in range(3):
        a = math.radians(i * 60 + 90)
        dx, dy = 32 * math.cos(a), 32 * math.sin(a)
        draw.line((cx - dx, cy - dy, cx + dx, cy + dy), fill="#7FA6D8", width=5)


HERO_SCALE = 2  # the hero is drawn at 2x, then downsampled for smooth edges
HERO_COLUMN = (88, 620)  # x range of the left text column in output pixels
HERO_MASCOT = (690, 66, 380)  # x, y and size of the main mascot in output pixels
HERO_TEXT = (
    # key, x, y, text, font size in output pixels
    ("title", 88, 190, "mascot-wardrobe", 64),
    ("tagline", 92, 282, "Generate once. Select by weather.", 34),
    ("subline", 92, 340, "Offline, deterministic outfits for your mascot.", 22),
)


def hero_layout() -> dict[str, tuple[int, int, int, int]]:
    """Output-pixel bounding boxes (x0, y0, x1, y1) of the hero pieces, measured with the real font."""
    boxes: dict[str, tuple[int, int, int, int]] = {}
    for key, x, y, text, size in HERO_TEXT:
        left, top, right, bottom = font(size).getbbox(text, anchor="lt")
        boxes[key] = (x + left, y + top, x + right, y + bottom)
    mx, my, msize = HERO_MASCOT
    alpha = render_mascot(msize).getchannel("A").point(lambda v: 255 if v > 0 else 0)
    left, top, right, bottom = alpha.getbbox()
    boxes["mascot"] = (mx + left, my + top, mx + right, my + bottom)
    boxes["column"] = (HERO_COLUMN[0], 0, HERO_COLUMN[1], 500)
    return boxes


def build_hero(outfits: list[dict]) -> Image.Image:
    scale = HERO_SCALE
    width, height = 1600 * scale, 500 * scale
    hero = gradient((width, height), "#DDF3E8", "#FFF4DC")
    # Soft background discs give depth without clutter.
    discs = Image.new("RGBA", hero.size)
    dd = ImageDraw.Draw(discs)
    dd.ellipse((-260 * scale, 250 * scale, 260 * scale, 770 * scale), fill=(191, 232, 213, 110))
    dd.ellipse((1180 * scale, -300 * scale, 1760 * scale, 280 * scale), fill=(255, 255, 255, 70))
    hero = Image.alpha_composite(hero, discs)
    draw = ImageDraw.Draw(hero)
    draw.rounded_rectangle((88 * scale, 150 * scale, 160 * scale, 158 * scale), radius=4 * scale, fill=CORAL)
    for key, x, y, text, size in HERO_TEXT:
        draw.text(
            (x * scale, y * scale),
            text,
            font=font(size * scale),
            fill=INK if key == "title" else MUTED,
            anchor="lt",
        )
    # Main mascot, bare, standing on a soft shadow.
    mx, my, msize = HERO_MASCOT
    main = render_mascot(msize * scale)
    hero.alpha_composite(main, (mx * scale, my * scale))
    # Outfit strip: cold -> mild -> hot, which reads as the weather changing.
    panel = card((470 * scale, 400 * scale), 40 * scale, fill=(255, 255, 255, 170))
    hero.alpha_composite(panel, (1070 * scale, 50 * scale))
    draw = ImageDraw.Draw(hero)
    picks = tuple(zip(HERO_PICKS, ("3 °C", "21 °C", "35 °C"), (snowflake, cloud, sun), strict=True))
    for slot, (index, temp, _glyph) in enumerate(picks):
        cx = hero_center(slot) * scale
        thumb = HERO_THUMB * scale
        small = outfits[index]["image"].resize((thumb, thumb), Image.Resampling.LANCZOS)
        hero.alpha_composite(small, (cx - thumb // 2, 200 * scale))
        draw = ImageDraw.Draw(hero)
        draw.text((cx, 392 * scale), temp, font=font(28 * scale), fill=INK, anchor="ms")
    # Glyphs are drawn at 1x geometry on a scaled layer so they stay crisp.
    glyphs = Image.new("RGBA", hero.size)
    gd = ImageDraw.Draw(glyphs)
    for slot, (_, _, glyph) in enumerate(picks):
        glyph_scaled(gd, glyph, hero_center(slot) * scale, 135 * scale, scale)
    hero = Image.alpha_composite(hero, glyphs)
    return hero.resize((1600, 500), Image.Resampling.LANCZOS).convert("RGB")


def glyph_scaled(draw: ImageDraw.ImageDraw, glyph, cx: float, cy: float, scale: int) -> None:
    """Draw a 1x glyph function at ``scale`` by wrapping the draw calls."""
    glyph(_ScaledDraw(draw, scale, cx, cy), 0, 0)


class _ScaledDraw:
    """Tiny adapter: glyph code uses local coordinates, this scales and offsets them."""

    def __init__(self, draw: ImageDraw.ImageDraw, scale: int, ox: float, oy: float):
        self.draw, self.s, self.ox, self.oy = draw, scale, ox, oy

    def _p(self, values):
        return [(v * self.s) + (self.ox if i % 2 == 0 else self.oy) for i, v in enumerate(values)]

    def ellipse(self, box, fill):
        self.draw.ellipse(self._p(box), fill=fill)

    def line(self, box, fill, width):
        self.draw.line(self._p(box), fill=fill, width=width * self.s)

    def rounded_rectangle(self, box, radius, fill):
        self.draw.rounded_rectangle(self._p(box), radius=radius * self.s, fill=fill)


# --------------------------------------------------------------------------- driver
def generate(root: Path) -> None:
    for name in OUTPUT_NAMES:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / "art/mascot.svg").write_text(build_svg(), encoding="utf-8")
    mascot_path = root / "examples/mascot.png"
    render_mascot(DESIGN).save(mascot_path, format="PNG", optimize=False)
    with tempfile.TemporaryDirectory() as work:
        outfits = build_catalog(mascot_path, Path(work))
    build_contact_sheet(outfits).save(root / "assets/contact-sheet.png", format="PNG", optimize=False)
    build_hero(outfits).save(root / "assets/hero.png", format="PNG", optimize=False)


def check() -> int:
    with tempfile.TemporaryDirectory() as work:
        fresh = Path(work)
        generate(fresh)
        problems = []
        if (REPO / "art/mascot.svg").read_bytes() != (fresh / "art/mascot.svg").read_bytes():
            problems.append("art/mascot.svg is out of date")
        for name in OUTPUT_NAMES[1:]:
            committed = REPO / name
            if not committed.is_file():
                problems.append(f"{name} is missing")
                continue
            with Image.open(committed) as old, Image.open(fresh / name) as new:
                if old.size != new.size:
                    problems.append(f"{name} has the wrong size")
                # Text rasterization depends on the Pillow wheel: the mascot is compared exactly, the
                # composite images by their outfit thumbnails only.
                elif name == "examples/mascot.png":
                    if old.convert("RGBA").tobytes() != new.convert("RGBA").tobytes():
                        problems.append(f"{name} is out of date")
                elif stale_portraits(name, old, new):
                    problems.append(f"{name} has outdated outfit thumbnails")
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=REPO, help="root directory to write into (default: repository)"
    )
    parser.add_argument("--check", action="store_true", help="verify committed art equals fresh output")
    args = parser.parse_args()
    if args.check:
        return check()
    generate(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
