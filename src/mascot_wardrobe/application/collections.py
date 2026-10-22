"""Discover named closet wardrobes and persist a separate weather-publisher choice.

Discovery and selection reads never open the inventory store or create directories.
"""

import hashlib
import json
import os
from pathlib import Path

from mascot_wardrobe.closet import SCHEMA_VERSION, Closet, _name
from mascot_wardrobe.infrastructure.inventory_store import _json_bytes, _safe_path, _write_atomic, data_home

SELECTION_FILE = "weather-publisher.json"
GATEWAY_LABELS = {
    "hermes": "Active Hermes PetDex pet/avatar (changes only with --apply)",
    "whatsapp": "WhatsApp profile photo (changes only with --apply)",
}


def _selector(home, root, wardrobe):
    identity = [Path(root).relative_to(home).as_posix(), wardrobe]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()[:24]


def _collection_manifest(root):
    """Read only collection metadata; renderer compatibility is a publishing concern.

    Legacy template sets use the same schema-1 wardrobe records. Listing/removing a name
    must not verify or regenerate their images, or require a renderer migration.
    """
    root = _safe_path(root)
    path = _safe_path(root / "closet.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{path} is not valid JSON") from error
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError(f"Unsupported closet schema in {path}")
    wardrobes = data.get("wardrobes")
    if not isinstance(wardrobes, dict):
        raise ValueError(f"Invalid wardrobes in {path}")
    for name, record in wardrobes.items():
        _name(name, "wardrobe name")
        if not isinstance(record, dict) or not isinstance(record.get("items"), list):
            raise ValueError(f"Malformed wardrobe {name} in {path}")
    active = data.get("active")
    if "active" not in data or (
        active is not None and (not isinstance(active, str) or active not in wardrobes)
    ):
        raise ValueError(f"Invalid active wardrobe in {path}")
    return root, data


def list_collections(home=None):
    """List every closet.json below the data home, without following symbolic links."""
    home = data_home(home)
    if not home.exists():
        return []
    rows = []

    def unreadable(error):
        raise error

    for directory, children, files in os.walk(home, followlinks=False, onerror=unreadable):
        children[:] = sorted(name for name in children if not (Path(directory) / name).is_symlink())
        if "closet.json" not in files:
            continue
        root, data = _collection_manifest(directory)
        for name, record in sorted(data["wardrobes"].items()):
            rows.append(
                {
                    "id": _selector(home, root, name),
                    "wardrobe": name,
                    "closet_root": str(root),
                    "item_count": len(record["items"]),
                    "active": data["active"] == name,
                }
            )
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Collection selector ID collision")
    return sorted(rows, key=lambda row: (row["closet_root"], row["wardrobe"]))


def find_collection(home, selector):
    matches = [row for row in list_collections(home) if row["id"] == selector]
    if len(matches) != 1:
        raise ValueError("Unknown collection selector ID; run 'collections list'")
    return matches[0]


def validate_gateways(gateways):
    if (
        not isinstance(gateways, (list, tuple))
        or not gateways
        or any(not isinstance(g, str) or g not in GATEWAY_LABELS for g in gateways)
    ):
        raise ValueError("Choose one or more gateways: hermes (active PetDex pet), whatsapp")
    return [gateway for gateway in GATEWAY_LABELS if gateway in gateways]


def save_publisher_selection(home, selector, gateways):
    home = data_home(home)
    gateways = validate_gateways(gateways)
    row = find_collection(home, selector)
    Closet.open(row["closet_root"], renderer_check=False).wardrobe_entries(
        row["wardrobe"], renderer_check=False
    )
    data = {
        "schema_version": 1,
        "selector_id": row["id"],
        "closet_root": row["closet_root"],
        "wardrobe": row["wardrobe"],
        "gateways": gateways,
    }
    path = _safe_path(home / SELECTION_FILE)
    _write_atomic(path, _json_bytes(data))
    return {
        **data,
        "selection_file": str(path),
        "gateway_labels": {gateway: GATEWAY_LABELS[gateway] for gateway in gateways},
    }


def read_publisher_selection(home=None):
    home = data_home(home)
    path = _safe_path(home / SELECTION_FILE)
    if not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("Missing or invalid weather publisher selection; run 'collections select'")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid weather publisher selection JSON") from error
    keys = {"schema_version", "selector_id", "closet_root", "wardrobe", "gateways"}
    if not isinstance(data, dict) or set(data) != keys or type(data["schema_version"]) is not int:
        raise ValueError("Invalid weather publisher selection")
    if data["schema_version"] != 1:
        raise ValueError("Unsupported weather publisher selection schema")
    validate_gateways(data["gateways"])
    _name(data["wardrobe"], "wardrobe name")
    if not isinstance(data["closet_root"], str) or not Path(data["closet_root"]).is_absolute():
        raise ValueError("Selection closet root must be an absolute path within the data home")
    root = _safe_path(data["closet_root"])
    if not root.is_relative_to(home) or data["selector_id"] != _selector(home, root, data["wardrobe"]):
        raise ValueError("Selection closet root or selector ID is invalid")
    Closet.open(root, renderer_check=False).wardrobe_entries(data["wardrobe"], renderer_check=False)
    return {**data, "gateways": validate_gateways(data["gateways"])}


def remove_collection(home, selector, *, yes=False):
    if not yes:
        raise ValueError("Removing a named collection requires explicit --yes; item data is retained")
    row = find_collection(home, selector)
    root, data = _collection_manifest(row["closet_root"])
    name = row["wardrobe"]
    if name not in data["wardrobes"]:
        raise ValueError(f"Unknown wardrobe {name}")
    if data["active"] == name:
        raise ValueError(f"Cannot remove active wardrobe {name}")
    del data["wardrobes"][name]
    _write_atomic(root / "closet.json", _json_bytes(data))
    return {
        **row,
        "removed": True,
        "retained": (
            "All item assets, catalog files and inventory are retained; only the wardrobe entry is removed"
        ),
    }
