"""Local deterministic generation: no diffusion, network or remote AI calls.

The normalized body box limits clothing to the body; there is no semantic segmentation.
Existing transparency is preserved; the background of an opaque PNG is not removed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mascot_wardrobe.domain.models import CountryProfile, Mascot

from PIL import Image

from mascot_wardrobe.wardrobe.composer import (  # noqa: F401
    CATEGORIES,
    LocalComposer,
    OutfitBrief,
    Renderer,
    _validate_brief,
)

# Row order of the 9-row state atlas layout (the exported atlas contract).
STATE_ROWS = (
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


def _atlas(portrait: Image.Image) -> Image.Image:
    """Simple geometric motion: six usable frames, two empty columns."""
    sprite = portrait.copy()
    sprite.thumbnail((170, 184), Image.Resampling.LANCZOS)
    atlas = Image.new("RGBA", (1536, 1872))
    for row, state in enumerate(STATE_ROWS):
        for frame, offset in enumerate((-3, -2, -1, 1, 2, 3)):
            dx, dy = offset, (frame % 3) - 1
            if state == "running-left":
                dx = -offset
            elif state == "jumping":
                dy = -(0, 5, 10, 12, 8, 3)[frame]
            elif state == "failed":
                dy += 4
            x = frame * 192 + (192 - sprite.width) // 2 + dx
            y = row * 208 + (208 - sprite.height) // 2 + dy
            atlas.alpha_composite(sprite, (x, y))
    return atlas


def _square_jpg(portrait: Image.Image) -> Image.Image:
    sprite = portrait.copy()
    sprite.thumbnail((512, 512), Image.Resampling.LANCZOS)
    square = Image.new("RGBA", (512, 512), "white")
    square.alpha_composite(sprite, ((512 - sprite.width) // 2, (512 - sprite.height) // 2))
    return square.convert("RGB")


def _safe_root(output_dir: Path) -> Path:
    root = Path(output_dir)
    if ".." in root.parts:
        raise ValueError("The output root does not accept directory traversal.")
    root = root.absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("The output root and its ancestors cannot be symbolic links.")
    if root.exists() and not root.is_dir():
        raise ValueError("The output root must be a directory.")
    return root


def _safe_target(root: Path, name: str) -> Path:
    root = _safe_root(root)
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.(?:png|jpg|json)", name):
        raise ValueError("Unsafe artifact name: only simple local names are accepted.")
    target = root / name
    if target.is_symlink() or target.resolve().parent != root:
        raise ValueError("The artifact must stay inside the output root without links.")
    return target


def _validate_request(mascot: Mascot, profile: CountryProfile) -> Path:
    if not isinstance(mascot.mascot_id, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9-]{0,63}", mascot.mascot_id
    ):
        raise ValueError("mascot_id must be a safe lowercase slug (up to 64 characters).")
    if not isinstance(profile.country_code, str) or not re.fullmatch(r"[A-Z]{2}", profile.country_code):
        raise ValueError("country_code must be two uppercase letters, without implicit normalization.")
    thresholds = profile.thresholds_c
    if (
        not isinstance(thresholds, (tuple, list))
        or len(thresholds) != len(CATEGORIES) - 1
        or any(not isinstance(v, (float, int)) or not math.isfinite(v) for v in thresholds)
        or any(a >= b for a, b in zip(thresholds, thresholds[1:], strict=False))
    ):
        raise ValueError("Six categories require five finite, strictly increasing boundaries.")
    if (
        not isinstance(profile.garments, (tuple, list))
        or not profile.garments
        or any(not isinstance(item, str) or not item.strip() for item in profile.garments)
    ):
        raise ValueError("garments requires non-empty garment instructions.")
    try:
        source_path = Path(mascot.image_path)
    except TypeError as exc:
        raise ValueError("The source must be a local PNG path.") from exc
    if (
        ".." in source_path.parts
        or ":" in str(source_path)
        or source_path.suffix.lower() != ".png"
        or source_path.is_symlink()
        or not source_path.is_file()
    ):
        raise ValueError("The source must be an existing local PNG file, without traversal or links.")
    return source_path


class WardrobeAgent:
    """One-time wardrobe preparation use case; no recurring inference.

    Injecting a Renderer connects a future real provider.
    Weather application only selects the files already produced.
    """

    def __init__(self, renderer: Renderer | None = None):
        self.renderer = renderer if renderer is not None else LocalComposer()

    def generate(self, mascot: Mascot, profile: CountryProfile, output_dir: Path) -> Path:
        source_path = _validate_request(mascot, profile)
        root = _safe_root(output_dir)
        try:
            with Image.open(source_path) as image:
                if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Only static PNG is accepted in this MVP.")
                if image.width * image.height > 16_777_216:
                    raise ValueError("PNG exceeds the 16 megapixel limit.")
                image.load()
                source = image.convert("RGBA")
        except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
            raise ValueError("Could not load a valid local PNG.") from exc
        outfits, pending = [], []
        hashes = set()
        target = _safe_target(root, "manifest.json")
        request = {
            "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "mascot_id": mascot.mascot_id,
            "body_box": list(mascot.body_box),
            "country_code": profile.country_code,
            "thresholds_c": list(profile.thresholds_c),
            "palette": list(profile.palette),
            "garments": list(profile.garments),
            "renderer": f"{type(self.renderer).__module__}.{type(self.renderer).__qualname__}",
        }
        try:
            # JSON snapshot: canonical lists with no mutable references.
            request["renderer_config"] = json.loads(
                json.dumps(getattr(self.renderer, "cache_config", None), sort_keys=True, allow_nan=False)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("The renderer cache_config must be valid finite JSON.") from exc
        metadata = {
            "schema_version": 1,
            "mascot_id": mascot.mascot_id,
            "country_code": profile.country_code,
            "generator": "local-compositor",
            "provenance": {
                "method": "deterministic local composition with Pillow",
                "remote_inference": False,
                "source_sha256": request["source_sha256"],
                "animation": "geometric translations, not semantic animation",
            },
            "atlas_layout": {
                "width": 1536,
                "height": 1872,
                "columns": 8,
                "rows": 9,
                "frame_width": 192,
                "frame_height": 208,
                "usable_frames_per_state": 6,
                "state_rows": list(STATE_ROWS),
            },
            "request": request,
        }
        if target.exists():
            try:
                manifest = json.loads(target.read_text(encoding="utf-8"))
                if (
                    any(manifest[key] != value for key, value in metadata.items())
                    or not isinstance(manifest["outfits"], list)
                    or len(manifest["outfits"]) != len(CATEGORIES)
                ):
                    raise ValueError("The manifest does not match the current request.")
                for category, outfit in zip(CATEGORIES, manifest["outfits"], strict=True):
                    slug = f"{mascot.mascot_id}-{profile.country_code.lower()}-{category.replace('_', '-')}"
                    expected_paths = {
                        "png": f"{slug}.png",
                        "jpg": f"{slug}.jpg",
                        "atlas": f"{slug}-atlas.png",
                    }
                    if outfit["category"] != category or outfit["slug"] != slug:
                        raise ValueError("Incomplete or duplicate categories in the manifest.")
                    for key, name in expected_paths.items():
                        if outfit[key] != name:
                            raise ValueError("Unexpected artifact path in the manifest.")
                        asset = _safe_target(root, outfit[key])
                        if (
                            not asset.is_file()
                            or hashlib.sha256(asset.read_bytes()).hexdigest() != outfit["sha256"][key]
                        ):
                            raise ValueError("Artifact integrity check failed.")
            except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("Invalid manifest or artifacts.") from exc
            return target
        for value in CATEGORIES:
            brief_text = (
                f"Country {profile.country_code}; category {value}; "
                f"palette {', '.join(profile.palette)}; garment instructions: "
                f"{'; '.join(profile.garments)}. Preserve face and silhouette; "
                "deterministic local template composition, no semantic diffusion."
            )
            brief = OutfitBrief(value, profile.palette, profile.garments, mascot.body_box, brief_text)
            _validate_brief(source, brief)
            portrait = self.renderer.render(source.copy(), brief)
            if (
                not isinstance(portrait, Image.Image)
                or portrait.mode != "RGBA"
                or portrait.size != source.size
            ):
                raise ValueError(
                    f"Renderer omitted or invalidated category {value}: expected a source-size RGBA portrait."
                )
            box = tuple(int(v * n) for v, n in zip(mascot.body_box, source.size * 2, strict=True))
            expected = source.copy()
            expected.paste(portrait.crop(box), box)
            if expected.tobytes() != portrait.tobytes():
                raise ValueError(f"Renderer changed pixels outside the body box in category {value}.")
            hashes.add(hashlib.sha256(portrait.tobytes()).hexdigest())
            slug = f"{mascot.mascot_id}-{profile.country_code.lower()}-{value.replace('_', '-')}"
            paths = {"png": f"{slug}.png", "jpg": f"{slug}.jpg", "atlas": f"{slug}-atlas.png"}
            for name in paths.values():
                if _safe_target(root, name).exists():
                    raise ValueError("Artifact already exists; it will not be overwritten.")
            pending.append((portrait, paths))
            outfits.append(dict(category=value, slug=slug, brief=brief_text, **paths))
        if len(hashes) != len(CATEGORIES):
            raise ValueError("The renderer must provide six visibly distinct outfits; check body_box.")
        manifest = {**metadata, "outfits": outfits}
        target = root / "manifest.json"
        temporary = None
        created = []
        root.mkdir(parents=True, exist_ok=True)
        try:
            for outfit, (portrait, paths) in zip(outfits, pending, strict=True):
                outfit["sha256"] = {}
                for key, image, image_format in (
                    ("png", portrait, "PNG"),
                    ("jpg", _square_jpg(portrait), "JPEG"),
                    ("atlas", _atlas(portrait), "PNG"),
                ):
                    destination = _safe_target(root, paths[key])
                    with destination.open("xb") as handle:
                        created.append(destination)
                        image.save(handle, format=image_format)
                    outfit["sha256"][key] = hashlib.sha256(destination.read_bytes()).hexdigest()
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=root, prefix=".manifest-", suffix=".tmp", delete=False
            ) as handle:
                temporary = Path(handle.name)
                json.dump(manifest, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except BaseException:
            for artifact in created:
                artifact.unlink(missing_ok=True)
            raise
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return target


__all__ = ["WardrobeAgent", "LocalComposer", "OutfitBrief", "Renderer", "STATE_ROWS"]
