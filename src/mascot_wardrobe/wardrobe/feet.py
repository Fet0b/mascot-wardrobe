"""Conservative alpha-only distal lobes; uncertainty means no sneakers.

This is a shape heuristic, not semantic segmentation. It looks for exactly two broad, short,
rounded terminal branches in the bottom 28% of the complete alpha silhouette. Detection never
uses the edit mask: a mask edge must not invent a foot. Returned masks exclude the outline,
translucent pixels and a scale-dependent interior margin.
"""

from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from mascot_wardrobe.wardrobe.styles import PaletteRoles


def _runs(row: bytes) -> list[tuple[int, int]]:
    runs = []
    start = None
    for x, value in enumerate(row + b"\0"):
        if value and start is None:
            start = x
        elif not value and start is not None:
            runs.append((start, x))
            start = None
    return runs


def detect_feet(alpha: Image.Image) -> tuple[Image.Image, ...]:
    """Return two source-size binary interior masks, ordered left to right, or an empty tuple.

    A stable split must persist to both tips without another branch, merging or extra islands.
    Both lobes must have comparable area, similar terminal height, a broad foot aspect and a
    tapering bottom. Cropped tips and long/narrow legs are ambiguous and rejected. No mascot,
    colour, body-box, or image-coordinate constants are used.
    """
    # Remove isolated one-pixel alpha noise for topology only; paint still requires original
    # alpha == 255 and an eroded interior. A third substantive branch remains ambiguous.
    binary = alpha.point(lambda v: 255 if v > 127 else 0).filter(ImageFilter.MedianFilter(3))
    bounds = binary.getbbox()
    if bounds is None:
        return ()
    left, top, right, bottom = bounds
    width, height = right - left, bottom - top
    if height < 24 or width < 24 or bottom == alpha.height:
        return ()
    crop = binary.crop(bounds)
    data = crop.tobytes()
    rows = [_runs(data[y * width : (y + 1) * width]) for y in range(height)]
    # Search the first stable two-branch split, rather than trimming arbitrary leg ends until
    # they happen to look wide enough. Rows containing a third lobe invalidate earlier cuts.
    start = max(round(height * 0.72), max((y + 1 for y, runs in enumerate(rows) if len(runs) > 2), default=0))
    for cut in range(start, height):
        if len(rows[cut]) != 2:
            continue
        tail = crop.crop((0, cut, width, height))
        components = []
        remainder = tail.copy()
        for x0, _x1 in rows[cut]:
            marked = tail.copy()
            ImageDraw.floodfill(marked, (x0, 0), 128)
            component = marked.point(lambda v: 255 if v == 128 else 0)
            components.append(component)
            remainder = ImageChops.subtract(remainder, component)
        if remainder.getbbox() or ImageChops.multiply(*components).getbbox():
            continue
        # Each branch is a single interval per occupied row. A hidden third foot connected
        # higher up is still a branch and cannot be silently absorbed into one sneaker.
        if any(
            len(_runs(component.crop((0, y, width, y + 1)).tobytes())) > 1
            for component in components
            for y in range(component.height)
        ):
            continue
        boxes = [component.getbbox() for component in components]
        areas = [component.histogram()[255] for component in components]
        if min(areas) < 0.45 * max(areas):
            return ()
        if abs(boxes[0][3] - boxes[1][3]) > max(2, height * 0.04):
            return ()
        if boxes[1][0] - boxes[0][2] < max(2, width * 0.02):
            return ()
        masks = []
        for component, (x0, y0, x1, y1) in zip(components, boxes, strict=True):
            w, h = x1 - x0, y1 - y0
            if not (height * 0.025 <= h <= height * 0.15 and 1.05 <= w / h <= 4.5):
                return ()
            if not width * 0.06 <= w <= width * 0.38:
                return ()
            lower = component.crop((x0, y0 + round(h * 0.75), x1, y1))
            if lower.histogram()[255] / max(1, lower.height) >= w * 0.8:
                return ()
            mask = Image.new("L", alpha.size)
            mask.paste(component, (left, top + cut))
            margin = max(1, round(height * 0.015))
            # Erode the real silhouette boundary, not the artificial horizontal branch cut.
            # The cut is a shoe opening; it is not part of the mascot outline.
            px, py = left + x0, top + cut + y0
            local = alpha.crop((px - margin, py - margin, px + w + margin, py + h + margin))
            local = local.point(lambda v: 255 if v == 255 else 0).filter(
                ImageFilter.MinFilter(2 * margin + 1)
            )
            interior = Image.new("L", alpha.size)
            interior.paste(local.crop((margin, margin, w + margin, h + margin)), (px, py))
            mask = ImageChops.multiply(mask, interior)
            if mask.getbbox() is None:
                return ()
            masks.append(mask)
        return tuple(masks)
    return ()


def shoe_overlay(alpha: Image.Image, roles: PaletteRoles, region: Image.Image | None = None) -> Image.Image:
    """Small black/base uppers, purple/secondary soles, lavender/accent toes and laces.

    Final binary clipping is exact: neither drawing nor antialiasing can escape a detected
    interior or an explicit protected pixel. Opaque source pixels keep their original alpha.
    """
    overlay = Image.new("RGBA", alpha.size)
    for index, mask in enumerate(detect_feet(alpha)):
        x0, y0, x1, y1 = mask.getbbox()
        w, h = x1 - x0, y1 - y0
        paint = Image.new("RGBA", alpha.size)
        draw = ImageDraw.Draw(paint)
        draw.rectangle((x0, y0, x1, y1), fill=(*roles.base, 255))
        draw.rectangle((x0, y0 + round(h * 0.74), x1, y1), fill=(*roles.secondary, 255))
        draw.ellipse(
            (
                x0 + w * (0.73 if index else -0.12),
                y0 + h * 0.32,
                x0 + w * (1.12 if index else 0.27),
                y0 + h * 0.82,
            ),
            fill=(*roles.accent, 255),
        )
        for share in (0.32, 0.46, 0.60):
            y = y0 + round(h * share)
            draw.line(
                (x0 + w * 0.36, y, x0 + w * 0.60, y),
                fill=(*roles.accent, 255),
                width=max(1, round(h * 0.045)),
            )
        if region is not None:
            mask = ImageChops.multiply(mask, region.point(lambda v: 255 if v == 255 else 0))
        paint.putalpha(mask)
        overlay = Image.alpha_composite(overlay, paint)
    return overlay
