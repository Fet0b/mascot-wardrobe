"""Pillow-only Codex/PetDex atlas from one exact saved mascot PNG, entirely in memory."""

import hashlib
import io
import math
from collections import deque
from dataclasses import dataclass

from PIL import Image

from mascot_wardrobe.wardrobe.profile_photo import _decode_png, compose_profile_image

FRAME_W, FRAME_H = 192, 208
COLUMNS, FRAMES = 8, 6
# agent.pet.constants.CODEX_STATE_ROWS; checked against the installed runtime before apply.
CODEX_STATE_ROWS = (
    "idle",
    "running-right",
    "running-left",
    "waving",
    "jumping",
    "failed",
    "waiting",
    "running",
    "review",
)


@dataclass(frozen=True)
class Atlas:
    png: bytes
    source_sha256: str
    background_removed: bool
    rows: tuple[str, ...] = CODEX_STATE_ROWS

    @property
    def sha256(self):
        return hashlib.sha256(self.png).hexdigest()

    @property
    def slug(self):
        return "closet-pet-" + self.sha256


def prepare_source(png):
    """Preserve transparent RGBA; remove only a flat, light, edge-connected opaque background.

    All border pixels must agree within 3 levels, and the background must be near-neutral.
    Flood fill never crosses the mascot to reach enclosed white facial/garment details.
    Ambiguous backgrounds require a reviewed transparent source supplied outside the closet.
    """
    image = _decode_png(png, "The item")
    alpha = image.getchannel("A")
    if alpha.getbbox() is None:
        raise ValueError("The item has no visible pixels")
    if alpha.getextrema()[0] < 255:
        return image, False
    width, height = image.size
    pixels = image.load()
    background = pixels[0, 0][:3]

    def matches(x, y):
        return max(abs(a - b) for a, b in zip(pixels[x, y][:3], background, strict=True)) <= 3

    border = (
        [(x, 0) for x in range(width)]
        + [(x, height - 1) for x in range(width)]
        + [(0, y) for y in range(height)]
        + [(width - 1, y) for y in range(height)]
    )
    if (
        min(background) < 220
        or max(background) - min(background) > 10
        or not all(matches(x, y) for x, y in border)
    ):
        raise ValueError("Ambiguous opaque background; supply a reviewed transparent PNG")
    seen = bytearray(width * height)
    queue = deque(border)
    removed = 0
    while queue:
        x, y = queue.popleft()
        index = y * width + x
        if seen[index]:
            continue
        seen[index] = 1
        if not matches(x, y):
            continue
        pixels[x, y] = (0, 0, 0, 0)
        removed += 1
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height and not seen[ny * width + nx]:
                queue.append((nx, ny))
    box = image.getchannel("A").getbbox()
    if box is None or not 0.15 <= removed / (width * height) <= 0.995:
        raise ValueError("Ambiguous opaque background: insufficient visible foreground or flat margin")
    return image, True


def _png(image):
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _frame(item, row, index):
    """Small state-specific whole-mascot motions; all six phases differ without adding artwork."""
    phase = 2 * math.pi * index / FRAMES
    dx = (-4, -2, 0, 2, 4, 1)[index]
    dy, angle, scale = 0, 0, 1.0
    if row == "idle":
        scale = 1 + 0.018 * math.sin(phase)
    elif row in ("running", "running-right", "running-left"):
        dy, angle = -round(4 * abs(math.sin(phase))), 3 * math.sin(phase)
        if row == "running-left":
            item = item.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    elif row == "waving":
        angle = 5 * math.sin(phase)
    elif row == "jumping":
        dy = -round(16 * (1 - math.cos(phase)) / 2)
    elif row == "failed":
        dy, angle = 5, 6 + 3 * math.sin(phase)
    elif row == "waiting":
        dy = round(2 * math.sin(phase))
    elif row == "review":
        angle = -5 + 2 * math.sin(phase)
    transformed = item.resize(
        (max(1, round(item.width * scale)), max(1, round(item.height * scale))), Image.Resampling.LANCZOS
    ).rotate(angle, Image.Resampling.BICUBIC, expand=True)
    frame = Image.new("RGBA", (FRAME_W, FRAME_H))
    frame.alpha_composite(
        transformed, ((FRAME_W - transformed.width) // 2 + dx, (FRAME_H - transformed.height) // 2 + dy)
    )
    return frame


def validate_atlas(png, *, rows=CODEX_STATE_ROWS):
    if tuple(rows) != CODEX_STATE_ROWS:
        raise ValueError("Hermes PetDex row taxonomy mismatch")
    sheet = _decode_png(png, "The atlas")
    if sheet.size != (COLUMNS * FRAME_W, len(rows) * FRAME_H):
        raise ValueError("Invalid Hermes atlas geometry: expected 1536x1872")
    for row in range(len(rows)):
        frames = []
        for column in range(COLUMNS):
            frame = sheet.crop((column * FRAME_W, row * FRAME_H, (column + 1) * FRAME_W, (row + 1) * FRAME_H))
            box = frame.getchannel("A").getbbox()
            if column >= FRAMES:
                if box is not None:
                    raise ValueError("Hermes atlas unused columns must be transparent")
            else:
                if box is None or box[0] < 8 or box[1] < 8 or box[2] > FRAME_W - 8 or box[3] > FRAME_H - 8:
                    raise ValueError("Hermes atlas frame violates alpha margins/bounds")
                frames.append(frame.tobytes())
        if len(set(frames)) != FRAMES:
            raise ValueError("Hermes atlas needs six different frames per row")
    with Image.open(io.BytesIO(_png(sheet))) as decoded:
        if decoded.convert("RGBA").tobytes() != sheet.tobytes():
            raise ValueError("Hermes atlas PNG round-trip failed")


def build_atlas(png, *, overlay=None):
    item, removed = prepare_source(png)
    if overlay is not None:
        item = compose_profile_image(_png(item), overlay=overlay)
    item = item.crop(item.getchannel("A").getbbox())
    item.thumbnail((140, 150), Image.Resampling.LANCZOS)
    sheet = Image.new("RGBA", (COLUMNS * FRAME_W, len(CODEX_STATE_ROWS) * FRAME_H))
    for row, name in enumerate(CODEX_STATE_ROWS):
        for index in range(FRAMES):
            sheet.paste(_frame(item, name, index), (index * FRAME_W, row * FRAME_H))
    encoded = _png(sheet)
    validate_atlas(encoded)
    return Atlas(encoded, hashlib.sha256(png).hexdigest(), removed)
