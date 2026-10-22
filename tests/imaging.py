"""Small PNG fixtures whose body can be measured by the compositor."""

from pathlib import Path

from PIL import Image, ImageDraw


def write_body_png(path: Path, color: str = "#555555", size: tuple[int, int] = (100, 100)) -> None:
    """A transparent PNG with a body ellipse that fills the default body box except at its bottom."""
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((0.15 * width, 0.45 * height, 0.85 * width, 0.96 * height), fill=color)
    image.save(path)
