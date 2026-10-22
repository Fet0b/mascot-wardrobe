"""Square JPEG profile photo from a saved item PNG; the mascot pixels are scaled, never redrawn."""

import io
import math
import re
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

SIZE = 1024
DEFAULT_BACKGROUND = "#102b32"
# Every visible pixel stays inside this share of the inscribed circle, so a circular crop
# (WhatsApp) never clips the mascot.
SAFE_RADIUS_FRACTION = 0.86
_VISIBLE_ALPHA = 8
_MAX_PIXELS = 16_777_216
MAX_OVERLAY_BYTES = 4 * 1024 * 1024


def parse_background(value):
    if not isinstance(value, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
        raise ValueError("Background must be an explicit #rrggbb colour")
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))


def _decode_png(data, what):
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                raise ValueError(f"{what} must be a static PNG")
            if image.width * image.height > _MAX_PIXELS:
                raise ValueError(f"{what} exceeds the 16 megapixel limit")
            image.load()
            return image.convert("RGBA")
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError(f"{what} is not a valid PNG") from error


def _farthest_visible_pixel(mask):
    """Distance from the mask's center to its farthest visible pixel (extreme pixels of each row)."""
    cx, cy = mask.width / 2, mask.height / 2
    farthest = 1.0
    for y in range(mask.height):
        row = mask.crop((0, y, mask.width, y + 1)).getbbox()
        if row is not None:
            dx = max(abs(row[0] - cx), abs(row[2] - cx))
            farthest = max(farthest, math.hypot(dx, abs(y + 0.5 - cy) + 0.5))
    return farthest


def _read_overlay(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_OVERLAY_BYTES:
        raise ValueError("Overlay must be a regular PNG file of at most 4 MiB")
    image = _decode_png(path.read_bytes(), "The overlay")
    if image.width != image.height:
        raise ValueError("Overlay must be square")
    return image.resize((SIZE, SIZE), Image.Resampling.LANCZOS) if image.width != SIZE else image


def _clip_to_safe_circle(overlay):
    """An external overlay can never paint where a circular crop would cut it off."""
    radius = SAFE_RADIUS_FRACTION * SIZE / 2
    circle = Image.new("L", (SIZE * 4, SIZE * 4), 0)  # drawn 4x then reduced for a smooth edge
    ImageDraw.Draw(circle).ellipse(
        [(SIZE / 2 - radius) * 4, (SIZE / 2 - radius) * 4, (SIZE / 2 + radius) * 4, (SIZE / 2 + radius) * 4],
        fill=255,
    )
    circle = circle.resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    overlay = overlay.copy()
    overlay.putalpha(ImageChops.multiply(overlay.getchannel("A"), circle))
    return overlay


def compose_profile_image(png_bytes, *, background=None, overlay=None):
    """Shared portrait/atlas composition; transparent background unless explicitly supplied."""
    color = (*parse_background(background), 255) if background is not None else (0, 0, 0, 0)
    item = _decode_png(png_bytes, "The item")
    mask = item.getchannel("A").point(lambda a: 255 if a > _VISIBLE_ALPHA else 0)
    box = mask.getbbox()
    if box is None:
        raise ValueError("The item has no visible pixels")
    item = item.crop(box)
    scale = SAFE_RADIUS_FRACTION * SIZE / 2 / _farthest_visible_pixel(mask.crop(box))
    item = item.resize((round(item.width * scale), round(item.height * scale)), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (SIZE, SIZE), color)
    canvas.alpha_composite(item, ((SIZE - item.width) // 2, (SIZE - item.height) // 2))
    if overlay is not None:
        canvas.alpha_composite(_clip_to_safe_circle(_read_overlay(overlay)))
    return canvas


def compose_profile_jpeg(png_bytes, *, background=DEFAULT_BACKGROUND, overlay=None):
    """Square JPEG with caller-supplied optional overlay; this project ships no badge artwork."""
    parse_background(background)
    canvas = compose_profile_image(png_bytes, background=background, overlay=overlay)
    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, "JPEG", quality=92, subsampling=0, optimize=True)
    from mascot_wardrobe.channels.live import validate_profile_jpeg

    return validate_profile_jpeg(buffer.getvalue())
