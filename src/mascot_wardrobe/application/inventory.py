"""Inventory use cases; rendering and persistence remain adapter responsibilities."""

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

from mascot_wardrobe.domain.inventory import AssetRef, origin_key
from mascot_wardrobe.domain.models import Category, to_celsius
from mascot_wardrobe.infrastructure.asset_formats import get_format, validate_companion
from mascot_wardrobe.infrastructure.policies import load_profile


def _safe_file(path):
    path = Path(path).absolute()
    if ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Asset paths cannot contain traversal or symbolic links")
    if not path.is_file():
        raise ValueError("Missing asset file")
    return path.resolve()


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stage(tx, number, sources, fmt):
    files = []
    for role, path, expected in sources:
        extension = get_format(fmt).extensions[0] if role == "portrait" else path.suffix
        staged = tx.stage_asset(number, role, path, extension=extension)
        if staged.sha256 != expected:
            raise ValueError("Asset sha256 mismatch while importing")
        snapshot = tx.staged_asset_path(staged)
        if role == "portrait":
            get_format(fmt).validate(snapshot)
        else:
            validate_companion(snapshot, role)
        files.append(staged)
    return AssetRef(format=fmt, files=tuple(files))


def _add(tx, *, sources, fmt="png", **fields):
    asset = _stage(tx, tx.inventory.next_number, sources, fmt)
    tx.inventory, garment = tx.inventory.add(asset=asset, **fields)
    return {"number": garment.number, "status": "imported"}


def register_asset(path, store, *, name, ranges, style=None, palette=None, fmt="png"):
    """Import a validated local asset once, identified by its content hash."""
    adapter = get_format(fmt)
    path = _safe_file(path)
    adapter.validate(path)
    digest = _sha(path)
    origin = {"kind": "asset", "source_sha256": digest}
    with store.transaction() as tx:
        existing = tx.inventory.find_by_origin_key(origin_key(origin))
        if existing:
            return {"number": existing.number, "status": "unchanged"}
        return _add(
            tx,
            sources=[("portrait", path, digest)],
            fmt=fmt,
            name=name,
            ranges=tuple(ranges),
            style=style,
            style_choice="user" if style else None,
            palette=tuple(palette or ()),
            palette_choice="user" if palette else None,
            origin=origin,
        )


def _manifest_records(manifest_path):
    manifest = _safe_file(manifest_path)
    if manifest.stat().st_size > 262144:
        raise ValueError("Manifest too large")
    with manifest.open("rb") as source:
        raw = source.read(262145)
    if len(raw) > 262144:
        raise ValueError("Manifest too large")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Manifest is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Manifest must be an object")
    outfits = data.get("outfits")
    if not isinstance(outfits, list) or len(outfits) != 6:
        raise ValueError("Manifest must contain exactly six outfits")
    if any(not isinstance(o, dict) or not isinstance(o.get("category"), str) for o in outfits):
        raise ValueError("Invalid manifest outfits")
    if {o["category"] for o in outfits} != {c.value for c in Category}:
        raise ValueError("Manifest must contain six distinct categories")
    request = data.get("request", {})
    provenance = data.get("provenance", {})
    if not isinstance(request, dict) or not isinstance(provenance, dict):
        raise ValueError("Manifest provenance must be an object")
    palette = request.get("palette")
    if palette is not None and not isinstance(palette, list):
        raise ValueError("Manifest palette must be a list of hexadecimal colors")
    records = []
    for outfit in outfits:
        slug = outfit.get("slug")
        if not isinstance(slug, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,100}", slug) is None:
            raise ValueError("Unsafe outfit slug")
        hashes = outfit.get("sha256", {})
        if not isinstance(hashes, dict):
            raise ValueError("Manifest asset hashes must be an object")
        sources = []
        for key, role, suffix in (
            ("png", "portrait", ".png"),
            ("jpg", "preview", ".jpg"),
            ("atlas", "atlas", "-atlas.png"),
        ):
            expected_name = slug + suffix
            if outfit.get(key) != expected_name:
                raise ValueError("Outfit filenames must match the local slug")
            path = _safe_file(manifest.parent / expected_name)
            if path.parent != manifest.parent:
                raise ValueError("Unsafe outfit asset")
            if role == "portrait":
                get_format("png").validate(path)
            else:
                validate_companion(path, role)
            digest = _sha(path)
            if key in hashes and hashes[key] != digest:
                raise ValueError(f"Manifest {key} sha256 mismatch")
            sources.append((role, path, digest))
        origin = {
            "kind": "manifest",
            "mascot_id": data.get("mascot_id", request.get("mascot_id")),
            "source_sha256": request.get("source_sha256", provenance.get("source_sha256")),
            "category": outfit["category"],
            "country_code": data.get("country_code", request.get("country_code")),
            "thresholds_c": request.get("thresholds_c"),
            "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "png_sha256": sources[0][2],
        }
        for key in ("renderer", "renderer_config"):
            if key in request:
                origin[key] = request[key]
        records.append(
            {
                "sources": sources,
                "name": slug.replace("-", " ")[:60],
                "ranges": (outfit["category"],),
                "style": request.get("style"),
                "style_choice": request.get("style_choice", "default"),
                "palette": tuple(request.get("palette") or ()),
                "palette_choice": request.get("palette_choice", "default"),
                "origin": origin,
            }
        )
    return records


def register_manifest(manifest_path, store):
    """Import all six catalog outfits atomically, without choosing defaults."""
    records = _manifest_records(manifest_path)
    result = {"imported": [], "skipped": []}
    with store.transaction() as tx:
        for record in records:
            existing = tx.inventory.find_by_origin_key(origin_key(record["origin"]))
            if existing:
                incoming = {role: digest for role, _, digest in record["sources"]}
                registered = {asset.role: asset.sha256 for asset in existing.asset.files}
                if incoming != registered:
                    raise ValueError(f"collision: manifest asset bytes differ for garment #{existing.number}")
                result["skipped"].append(existing.number)
            else:
                result["imported"].append(_add(tx, **record)["number"])
    return result


def inventory_ranges(country, profiles=None):
    """Describe the six named project ranges using a country's policy."""
    profile = load_profile(country, profiles)
    bounds = (None, *profile.thresholds_c, None)
    return [
        {"range": category.value, "min_c": bounds[index], "max_c": bounds[index + 1]}
        for index, category in enumerate(Category)
    ]


def inventory_view(store, range=None):
    """Return every item in number order; suitability is only a display hint."""
    if range is not None and range not in {category.value for category in Category}:
        raise ValueError("Unknown inventory range")
    return [
        {
            "number": garment.number,
            "name": garment.name,
            "style": garment.style,
            "palette": list(garment.palette),
            "ranges": list(garment.ranges),
            "format": garment.asset.format,
            "version": garment.version,
            "suitable": range in garment.ranges,
        }
        for garment in sorted(store.read().garments, key=lambda garment: garment.number)
    ]


def set_default(store, range, number):
    """Save exactly the user's chosen item, independently of suitability."""
    with store.transaction() as tx:
        tx.inventory = tx.inventory.set_default(range, number)
    return {"range": range, "number": number, "status": "saved"}


def remove_garment(store, number):
    """Retire an unused number; defaults and active items cannot be removed."""
    with store.transaction() as tx:
        tx.inventory = tx.inventory.remove(number)
    return {"number": number, "status": "removed"}


def _classified(country, temperature, unit, profiles):
    profile = load_profile(country, profiles)
    temperature_c = to_celsius(temperature, unit)
    category = profile.classify(temperature_c).value
    return profile, category, temperature_c


def _resolved(inventory, profile, category, temperature_c):
    number = inventory.default_for(category)
    result = {"status": "no-default", "category": category, "temperature_c": temperature_c}
    if number is not None:
        result.update(status="default", number=number, country_code=profile.country_code)
    return result


def resolve_default(store, country, temperature, unit="C", profiles=None):
    """Resolve a user mapping without activating it or writing anything."""
    profile, category, temperature_c = _classified(country, temperature, unit, profiles)
    return _resolved(store.read(), profile, category, temperature_c)


def select_from_inventory(store, country, temperature, unit="C", profiles=None):
    """Activate only a saved mapping, atomically with the inventory snapshot."""
    profile, category, temperature_c = _classified(country, temperature, unit, profiles)
    with store.transaction() as tx:
        result = _resolved(tx.inventory, profile, category, temperature_c)
        if result["status"] == "no-default":
            return result
        garment = tx.inventory.describe(result["number"])
        result.update(
            status="selected",
            assets={asset.role: str(store.asset_path(asset)) for asset in garment.asset.files},
        )
        tx.inventory = replace(tx.inventory, active=garment.number)
        return result


def _closet_origin(closet, item_id):
    return {
        "kind": "closet",
        "mascot_sha256": closet.mascot_sha256,
        "mask_sha256": closet.mask_sha256,
        "item": item_id,
        "history": [],
    }


def _portrait_hash(garment):
    return next(asset.sha256 for asset in garment.asset.files if asset.role == "portrait")


def _collision(item_id, number):
    return ValueError(
        f"collision: item id {item_id} is already registered for this mascot and mask "
        f"as garment #{number} from another closet or an earlier state; choose a different item id"
    )


def closet_item_id_free(closet, item_id, store):
    """Check identity uniqueness under the inventory lock before rendering."""
    if store is None:
        return True
    with store.transaction() as tx:
        return tx.inventory.closet_item_id_free(closet.mascot_sha256, closet.mask_sha256, item_id)


def require_closet_item_id_free(closet, item_id, store):
    """Refuse a taken identity before a caller changes its closet."""
    if store is None:
        return
    with store.transaction() as tx:
        existing = tx.inventory.find_by_origin_key(origin_key(_closet_origin(closet, item_id)))
        if existing is not None:
            raise _collision(item_id, existing.number)


def _closet_record(closet, item_id):
    record = closet.items().get(item_id)
    if record is None:
        raise ValueError(f"Unknown closet item: {item_id}")
    path = _safe_file(closet.item_path(item_id))
    get_format("png").validate(path)
    digest = _sha(path)
    if digest != record["sha256"]:
        raise ValueError("Closet item sha256 mismatch")
    return record, path, digest


def _register_closet_item(
    tx, closet, item_id, prepared, *, ranges=(), replaces=None, allow_first_registration=False
):
    record, path, digest = prepared
    origin = _closet_origin(closet, item_id)
    existing = tx.inventory.find_by_origin_key(origin_key(origin))
    fields = {key: record.get(key) for key in ("name", "style", "style_choice", "palette_choice")}
    fields["palette"] = tuple(record.get("palette") or ())
    previous_ranges = existing.ranges if existing is not None else ()
    fields["ranges"] = tuple(dict.fromkeys((record["template"], *previous_ranges, *ranges)))
    if existing is None:
        if replaces is not None and not allow_first_registration:
            raise ValueError(f"Cannot replace unregistered closet item {item_id}")
        # Automatic edits can be the first registration of a pre-inventory closet.
        # No existing identity means this allocates a garment instead of replacing one.
        return _add(tx, sources=[("portrait", path, digest)], origin=origin, **fields)
    current_hash = _portrait_hash(existing)
    if replaces is not None and replaces != current_hash:
        raise _collision(item_id, existing.number)
    if digest != current_hash:
        migrated_from = record.get("migrated_from", {})
        migration_guard = migrated_from.get("sha256") if isinstance(migrated_from, dict) else None
        if replaces != current_hash and migration_guard != current_hash:
            raise _collision(item_id, existing.number)
        asset = _stage(tx, existing.number, [("portrait", path, digest)], "png")
        tx.inventory = tx.inventory.replace_asset(existing.number, asset)
        existing = tx.inventory.describe(existing.number)
        updated = replace(existing, **fields)
        status = "updated"
    else:
        updated = replace(existing, **fields)
        status = "unchanged" if updated == existing else "updated"
    if updated != existing:
        tx.inventory = replace(
            tx.inventory,
            garments=tuple(
                updated if garment.number == existing.number else garment for garment in tx.inventory.garments
            ),
        )
    return {"number": existing.number, "status": status}


def register_closet_item(closet, item_id, store, *, ranges=(), replaces=None):
    """Register one closet item, replacing bytes only with a matching hash guard."""
    prepared = _closet_record(closet, item_id)
    with store.transaction() as tx:
        return _register_closet_item(
            tx, closet, item_id, prepared, ranges=ranges, replaces=replaces, allow_first_registration=True
        )


def register_closet(closet, store, *, item_id=None, replaces=None, ranges=(), allow_first_registration=False):
    """Import a closet atomically, or perform its explicit single-item recovery."""
    if allow_first_registration and (item_id is None or replaces is None):
        raise ValueError("--allow-first-registration requires --item and --replaces")
    if replaces is not None and item_id is None:
        raise ValueError("--replaces requires --item")
    if ranges and item_id is None:
        raise ValueError("--range requires --item")
    item_ids = [item_id] if item_id is not None else list(closet.items())
    prepared = {key: _closet_record(closet, key) for key in item_ids}
    result = {"imported": [], "skipped": []}
    with store.transaction() as tx:
        for key in item_ids:
            item = _register_closet_item(
                tx,
                closet,
                key,
                prepared[key],
                replaces=replaces,
                ranges=ranges,
                allow_first_registration=allow_first_registration,
            )
            bucket = {"imported": "imported", "unchanged": "skipped", "updated": "updated"}[item["status"]]
            result.setdefault(bucket, []).append(item["number"])
    return result
