"""Explicit image-format adapters; inventory policies do not depend on Pillow."""

from pathlib import Path
from typing import Protocol

from PIL import Image

MAX_PIXELS = 16_777_216


class AssetFormat(Protocol):
    name: str
    extensions: tuple[str, ...]

    def validate(self, path: Path) -> dict: ...


def _validate(path, expected):
    path = Path(path).absolute()
    if ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Assets cannot contain traversal or symbolic links")
    try:
        with Image.open(path) as image:
            if image.format != expected or getattr(image, "n_frames", 1) != 1:
                raise ValueError(f"Asset must be a static {expected}.")
            if image.width * image.height > MAX_PIXELS:
                raise ValueError("Asset exceeds the 16 megapixel limit.")
            image.load()
            return {"size": image.size, "mode": image.mode}
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError(f"Asset is not a valid {expected}.") from exc


class PngFormat:
    name = "png"
    extensions = (".png",)

    def validate(self, path):
        return _validate(path, "PNG")


FORMATS = {"png": PngFormat()}


def get_format(name):
    try:
        return FORMATS[name]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"format {name!r} is not supported; supported: png") from exc


def validate_companion(path, role):
    """Validate the current PNG adapter's optional JPEG preview or PNG atlas."""
    path = Path(path)
    formats = {"preview": ((".jpg", ".jpeg"), "JPEG"), "atlas": ((".png",), "PNG")}
    if role not in formats:
        raise ValueError(f"Unsupported companion role: {role}")
    extensions, expected = formats[role]
    if path.suffix.lower() not in extensions:
        raise ValueError(f"Invalid extension for companion role {role}")
    return _validate(path, expected)
