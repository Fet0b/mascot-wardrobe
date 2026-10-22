"""Only explicitly registered static raster adapters are accepted."""

import pytest
from PIL import Image

from mascot_wardrobe.infrastructure.asset_formats import get_format, validate_companion


def test_png_adapter_validates_actual_decoded_format(tmp_path):
    path = tmp_path / "portrait.png"
    Image.new("RGBA", (3, 4)).save(path)
    assert get_format("png").validate(path) == {"size": (3, 4), "mode": "RGBA"}
    Image.new("RGB", (3, 4)).save(path, format="JPEG")
    with pytest.raises(ValueError, match="static PNG"):
        get_format("png").validate(path)
    for name in ("gif", "svg", "lottie"):
        with pytest.raises(ValueError, match="not supported; supported: png"):
            get_format(name)


def test_companions_require_known_role_extension_and_actual_encoding(tmp_path):
    preview = tmp_path / "preview.jpg"
    atlas = tmp_path / "atlas.png"
    Image.new("RGB", (4, 5)).save(preview)
    Image.new("RGBA", (5, 6)).save(atlas)
    assert validate_companion(preview, "preview")["size"] == (4, 5)
    assert validate_companion(atlas, "atlas")["size"] == (5, 6)
    with pytest.raises(ValueError):
        validate_companion(atlas, "preview")
    with pytest.raises(ValueError):
        validate_companion(preview, "unexpected")
    Image.new("RGB", (4, 5)).save(preview, format="PNG")
    with pytest.raises(ValueError):
        validate_companion(preview, "preview")


def test_adapter_rejects_animation_size_invalid_and_symlink(tmp_path, monkeypatch):
    path = tmp_path / "portrait.png"
    frames = [Image.new("RGBA", (2, 2), color) for color in ("red", "blue")]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    with pytest.raises(ValueError, match="static PNG"):
        get_format("png").validate(path)
    Image.new("RGBA", (2, 2)).save(path)
    monkeypatch.setattr("mascot_wardrobe.infrastructure.asset_formats.MAX_PIXELS", 3)
    with pytest.raises(ValueError, match="megapixel"):
        get_format("png").validate(path)
    path.write_bytes(b"not png")
    with pytest.raises(ValueError, match="valid PNG"):
        get_format("png").validate(path)
    link = tmp_path / "link.png"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="symbolic"):
        get_format("png").validate(link)
