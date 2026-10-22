"""Application use cases: prepare once, select often, synchronize only sandbox."""

import hashlib
import json
import re
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

from mascot_wardrobe.domain.models import Category, Mascot, to_celsius
from mascot_wardrobe.infrastructure.policies import load_profile
from mascot_wardrobe.wardrobe import StyleChoice, WardrobeAgent, _validate_request
from mascot_wardrobe.wardrobe.styles import resolve_palette, resolve_style


def safe_output(path):
    """Resolve an output directory, rejecting traversal and symbolic links."""
    raw = Path(path).absolute()
    if ".." in raw.parts or any(p.is_symlink() for p in (raw, *raw.parents)):
        raise ValueError("Output paths cannot contain traversal or symbolic links")
    return raw.resolve()


def prepare_wardrobe(
    image,
    mascot_id,
    country,
    output,
    *,
    body_box=(0.22, 0.60, 0.78, 0.88),
    profiles=None,
    style=None,
    palette=None,
):
    """Prepare the six-outfit catalog and return the manifest path.

    ``style`` is a style name or ``"auto"``; ``palette`` a list of hex colors or ``"auto"``.
    ``None`` keeps the library default (classic, country profile palette). ``auto`` is resolved
    deterministically from the mascot hash and the country, locally.
    """
    mascot = Mascot(mascot_id, Path(image), body_box)
    profile = load_profile(country, profiles)
    choice = None
    if style is not None or palette is not None:
        source = _validate_request(mascot, profile)  # rejects links and non-PNG before any read
        seed = hashlib.sha256(source.read_bytes()).hexdigest() + profile.country_code
        resolved, style_source = resolve_style("classic" if style is None else style, "catalog", seed)
        if palette is None:
            colors, palette_source = tuple(profile.palette), "profile"
        else:
            colors, palette_source = resolve_palette(palette, resolved, "catalog", seed)
        choice = StyleChoice(
            resolved,
            colors,
            "default" if style is None else style_source,
            palette_source,
        )
    return WardrobeAgent().generate(mascot, profile, safe_output(output), choice=choice)


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


@dataclass(frozen=True)
class WhatsAppTarget:
    """Approved local photo directory read by the bridge, plus the adapter that talks to it."""

    catalog_dir: Path
    adapter: object


@dataclass(frozen=True)
class TelegramTarget:
    """Bot profile adapter plus a state directory holding the hash of the last acknowledged image."""

    adapter: object
    state_dir: Path


@contextmanager
def prepare_whatsapp_delivery(target, selection, *, preflight=False):
    """Hold the built-in adapter lock across staging, preflight and delivery."""
    from mascot_wardrobe.channels.live import HermesWhatsAppProfileAdapter, publish_to_catalog

    adapter = target.adapter
    managed = isinstance(adapter, HermesWhatsAppProfileAdapter)
    with adapter.locked() if managed else nullcontext():
        publish_to_catalog(selection["slug"], Path(selection["jpg"]), target.catalog_dir)
        if preflight:
            adapter.preflight(selection["slug"])
        sync = adapter._sync if managed else adapter.sync
        yield lambda: {"status": "applied", **sync(selection["slug"])}


def _publish_whatsapp(target, selection):
    with prepare_whatsapp_delivery(target, selection) as deliver:
        return deliver()


def _read_telegram_state(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Unsafe Telegram state file")
    if not path.exists() or path.stat().st_size >= 4096:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data.get("sha256") if isinstance(data, dict) else None


def _publish_telegram(target, selection, digest, force):
    from mascot_wardrobe.channels import _atomic

    directory = safe_output(target.state_dir)
    state = directory / "telegram-profile.json"
    if directory.exists() and not directory.is_dir():
        raise ValueError("Telegram state directory is not a directory")
    if not force and _read_telegram_state(state) == digest:
        return {"status": "unchanged", "acknowledged": True, "verified": False}
    result = target.adapter.apply(Path(selection["jpg"]))
    directory.mkdir(parents=True, exist_ok=True)
    if state.is_symlink() or state.resolve().parent != directory:
        raise ValueError("Unsafe Telegram state file")
    _atomic(state, json.dumps({"sha256": digest, "verified": False}).encode())
    return {"status": "applied", **result}


def publish_outfit(selection, *, apply=False, force=False, whatsapp=None, telegram=None):
    """Deliver one already-selected outfit to the configured live channels; dry-run unless ``apply``."""
    from mascot_wardrobe.channels.live import validate_profile_jpeg

    if apply and whatsapp is None and telegram is None:
        raise ValueError("Applying requires at least one configured channel")
    data = validate_profile_jpeg(Path(selection["jpg"]).read_bytes())
    digest = hashlib.sha256(data).hexdigest()
    channels = {}
    for name, target in (("whatsapp", whatsapp), ("telegram", telegram)):
        if target is None:
            continue
        if not apply:
            channels[name] = {"status": "planned"}
            continue
        try:
            if name == "whatsapp":
                channels[name] = _publish_whatsapp(target, selection)
            else:
                channels[name] = _publish_telegram(target, selection, digest, force)
        except (ValueError, OSError, RuntimeError) as error:
            channels[name] = {"status": "failed", "error": str(error)}
    return {
        "mode": "apply" if apply else "dry-run",
        "slug": selection["slug"],
        "category": selection["category"],
        "jpg": selection["jpg"],
        "atlas": selection.get("atlas"),
        "jpg_sha256": digest,
        "channels": channels,
        "ok": all(item["status"] != "failed" for item in channels.values()),
    }


# Inventory use cases load after the legacy application definitions.
from mascot_wardrobe.application.inventory import (  # noqa: E402, F401
    closet_item_id_free,
    inventory_ranges,
    inventory_view,
    register_asset,
    register_closet,
    register_closet_item,
    register_manifest,
    remove_garment,
    require_closet_item_id_free,
    resolve_default,
    select_from_inventory,
    set_default,
)
