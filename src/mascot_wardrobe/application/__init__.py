"""Application use cases: prepare once, select often, synchronize only sandbox."""

import json
import re
from pathlib import Path

from mascot_wardrobe.domain.models import Category, Mascot, to_celsius
from mascot_wardrobe.infrastructure.policies import load_profile
from mascot_wardrobe.wardrobe import WardrobeAgent


def safe_output(path):
    """Resolve an output directory, rejecting traversal and symbolic links."""
    raw = Path(path).absolute()
    if ".." in raw.parts or any(p.is_symlink() for p in (raw, *raw.parents)):
        raise ValueError("Output paths cannot contain traversal or symbolic links")
    return raw.resolve()


def prepare_wardrobe(image, mascot_id, country, output, *, body_box=(0.22, 0.60, 0.78, 0.88), profiles=None):
    return WardrobeAgent().generate(
        Mascot(mascot_id, Path(image), body_box), load_profile(country, profiles), safe_output(output)
    )


def export_outfit(selection, output, channel):
    from mascot_wardrobe.channels import FileExportChannel

    return FileExportChannel(safe_output(output), channel).export(
        selection["slug"], Path(selection["jpg"]), Path(selection["atlas"]), Path(selection["manifest"])
    )


def sync_sandbox(selection, state):
    from mascot_wardrobe.channels import SandboxBridge, WhatsAppBridgeChannel

    state = safe_output(state)
    catalog = Path(selection["jpg"]).parent
    with SandboxBridge(catalog, state) as bridge:
        return WhatsAppBridgeChannel(bridge.base_url).sync(selection["slug"])


def select_outfit(manifest, country, temperature, unit="C", *, profiles=None):
    manifest = Path(manifest).resolve()
    if manifest.stat().st_size > 262144:
        raise ValueError("Manifest too large")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    profile = load_profile(country, profiles)
    if not isinstance(data, dict) or data.get("country_code") != profile.country_code:
        raise ValueError("Manifest country must match the explicit country policy")
    temperature_c = to_celsius(temperature, unit)
    category = profile.classify(temperature_c).value
    outfits = data.get("outfits")
    if not isinstance(outfits, list) or len(outfits) != 6:
        raise ValueError("Manifest must contain exactly six outfits")
    if any(not isinstance(outfit, dict) for outfit in outfits):
        raise ValueError("Invalid manifest outfits")
    if any(not isinstance(outfit.get("category"), str) for outfit in outfits):
        raise ValueError("Outfit category must be a string")
    if {o.get("category") for o in outfits} != {c.value for c in Category}:
        raise ValueError("Manifest must contain six distinct categories")
    selected = next(o for o in outfits if o["category"] == category)
    slug = selected.get("slug")
    if not isinstance(slug, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,100}", slug) is None:
        raise ValueError("Unsafe outfit slug")
    result = {
        "category": category,
        "slug": slug,
        "temperature_c": temperature_c,
        "country_code": profile.country_code,
        "manifest": str(manifest),
    }
    for key, expected in (("jpg", slug + ".jpg"), ("png", slug + ".png"), ("atlas", slug + "-atlas.png")):
        name = selected.get(key)
        if name != expected:
            raise ValueError("Outfit filenames must match the local slug")
        path = manifest.parent / name
        if path.is_symlink() or path.resolve().parent != manifest.parent or not path.is_file():
            raise ValueError("Missing or unsafe outfit asset")
        result[key] = str(path)
    return result
