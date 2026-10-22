"""Pure numbered wardrobe values and policies; asset formats are opaque labels."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from mascot_wardrobe.domain.models import Category


@dataclass(frozen=True)
class AssetFile:
    role: str
    path: str
    sha256: str

    def __post_init__(self):
        _text(self.role, "Asset role")
        _hash(self.sha256)
        if (
            not isinstance(self.path, str)
            or not self.path
            or self.path.startswith("/")
            or any(part in ("", ".", "..") for part in self.path.split("/"))
            or "\\" in self.path
            or ":" in self.path
            or not self.path.isprintable()
        ):
            raise ValueError("Asset path must be a safe relative path.")


@dataclass(frozen=True)
class AssetRef:
    format: str
    files: tuple[AssetFile, ...]

    def __post_init__(self):
        _text(self.format, "Asset format")
        if not isinstance(self.files, (tuple, list)) or not self.files:
            raise ValueError("Asset files must be a non-empty sequence.")
        object.__setattr__(self, "files", tuple(self.files))
        if any(not isinstance(f, AssetFile) for f in self.files):
            raise ValueError("Invalid asset file.")
        if len({f.role for f in self.files}) != len(self.files) or len({f.path for f in self.files}) != len(
            self.files
        ):
            raise ValueError("Asset roles and paths must be distinct.")


@dataclass(frozen=True)
class Garment:
    number: int
    name: str
    asset: AssetRef
    origin: Mapping
    style: str | None = None
    style_choice: str | None = None
    palette: tuple[str, ...] = ()
    palette_choice: str | None = None
    ranges: tuple[str, ...] = ()
    version: int = 1

    def __post_init__(self):
        _positive(self.number, "Garment number")
        _positive(self.version, "Garment version")
        object.__setattr__(self, "name", _text(self.name, "name", limit=60).strip())
        if not isinstance(self.asset, AssetRef):
            raise ValueError("Garment asset must be an AssetRef.")
        for name in ("style", "style_choice", "palette_choice"):
            if getattr(self, name) is not None:
                _text(getattr(self, name), name)
        if not isinstance(self.palette, (list, tuple)) or len(self.palette) > 6:
            raise ValueError("Palette must contain one to six hexadecimal colors, or be unspecified.")
        if any(not isinstance(c, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", c) is None for c in self.palette):
            raise ValueError("Palette colors must be #RRGGBB hexadecimal colors.")
        if not isinstance(self.ranges, (list, tuple)) or not self.ranges:
            raise ValueError("Ranges must be a non-empty sequence of named categories.")
        for category in self.ranges:
            _category(category)
        if len(set(self.ranges)) != len(self.ranges):
            raise ValueError("Suitability ranges must be distinct.")
        object.__setattr__(self, "palette", tuple(self.palette))
        object.__setattr__(self, "ranges", tuple(self.ranges))
        origin_key(self.origin)
        if "history" in self.origin:
            if not isinstance(self.origin["history"], (list, tuple)):
                raise ValueError("Origin history must be a hash sequence.")
            for digest in self.origin["history"]:
                _hash(digest)
        object.__setattr__(self, "origin", _freeze(self.origin))


@dataclass(frozen=True)
class Inventory:
    schema_version: int = 1
    next_number: int = 1
    garments: tuple[Garment, ...] = ()
    retired: tuple[int, ...] = ()
    defaults: Mapping[str, int] = field(default_factory=dict)
    active: int | None = None

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported inventory schema version; expected 1.")
        _positive(self.next_number, "Next garment number")
        if not isinstance(self.garments, (list, tuple)) or not isinstance(self.retired, (list, tuple)):
            raise ValueError("Garments and retired numbers must be sequences.")
        if any(not isinstance(g, Garment) for g in self.garments):
            raise ValueError("Invalid garment record.")
        for number in self.retired:
            _positive(number, "Retired number")
        numbers = [g.number for g in self.garments]
        used = numbers + list(self.retired)
        if len(used) != len(set(used)):
            raise ValueError("Live and retired garment numbers must be distinct.")
        if used and self.next_number <= max(used):
            raise ValueError("Next garment number must exceed all live and retired numbers.")
        keys = [origin_key(g.origin) for g in self.garments]
        if len(keys) != len(set(keys)):
            raise ValueError("Garment origin identities must be distinct.")
        if not isinstance(self.defaults, Mapping):
            raise ValueError("Defaults must be a range-to-number mapping.")
        for category, number in self.defaults.items():
            _category(category)
            _positive(number, "Default garment number")
            if number not in numbers:
                raise ValueError(f"Default {category} references missing garment #{number}.")
        if self.active is not None:
            _positive(self.active, "Active garment number")
            if self.active not in numbers:
                raise ValueError("Active garment number does not exist.")
        object.__setattr__(self, "garments", tuple(self.garments))
        object.__setattr__(self, "retired", tuple(self.retired))
        object.__setattr__(self, "defaults", MappingProxyType(dict(self.defaults)))

    def add(self, **fields):
        if fields.get("version", 1) != 1 or type(fields.get("version", 1)) is not int:
            raise ValueError("New garments start at version 1.")
        garment = Garment(number=self.next_number, **fields)
        return replace(self, next_number=self.next_number + 1, garments=(*self.garments, garment)), garment

    def describe(self, number):
        if type(number) is not int or number < 1:
            raise ValueError("Garment number must be a positive integer.")
        for garment in self.garments:
            if garment.number == number:
                return garment
        raise ValueError(f"Unknown garment #{number}.")

    def remove(self, number):
        self.describe(number)
        users = [category for category, selected in self.defaults.items() if selected == number]
        if users or self.active == number:
            reason = ", ".join(users + (["active"] if self.active == number else []))
            raise ValueError(f"Garment #{number} is used by {reason}.")
        return replace(
            self,
            garments=tuple(g for g in self.garments if g.number != number),
            retired=(*self.retired, number),
        )

    def set_default(self, category, number):
        _category(category)
        self.describe(number)
        return replace(self, defaults={**self.defaults, category: number})

    def default_for(self, category):
        _category(category)
        return self.defaults.get(category)

    def find_by_origin_key(self, key):
        return next((g for g in self.garments if origin_key(g.origin) == key), None)

    def closet_item_id_free(self, mascot_sha256, mask_sha256, item_id):
        return self.find_by_origin_key(("closet", mascot_sha256, mask_sha256, item_id)) is None

    def replace_asset(self, number, asset):
        previous = self.describe(number)
        portrait = next((f.sha256 for f in previous.asset.files if f.role == "portrait"), None)
        history = list(previous.origin.get("history", ()))
        if portrait is not None:
            history.append(portrait)
        garment = replace(
            previous,
            asset=asset,
            version=previous.version + 1,
            origin={**previous.origin, "history": history},
        )
        return replace(self, garments=tuple(garment if g.number == number else g for g in self.garments))


def _category(value):
    if not isinstance(value, str) or value not in {c.value for c in Category}:
        raise ValueError("Range must be a named project category.")
    return value


def _positive(value, label):
    if type(value) is not int or value < 1:
        raise ValueError(f"{label} must be a positive integer.")


def _hash(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Asset hash must be a lowercase SHA-256 digest.")
    return value


def _text(value, label, *, limit=None):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    value = value.strip()
    if limit is not None and len(value) > limit:
        raise ValueError(f"{label} is too long; the limit is {limit} characters.")
    if not all(c.isprintable() or unicodedata.category(c) == "Zs" for c in value):
        raise ValueError(f"{label} must be a single line without control characters.")
    return value


def _freeze(value):
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise ValueError("Origin JSON object keys must be strings.")
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("Origin must contain finite JSON values only.")


def _thaw(value):
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(v) for v in value]
    return value


def origin_key(origin):
    if not isinstance(origin, Mapping):
        raise ValueError("Garment origin must be an object.")
    kind = origin.get("kind")
    try:
        if kind == "closet":
            item = origin["item"]
            if not isinstance(item, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", item) is None:
                raise ValueError("Closet item must be a lowercase slug of up to 40 characters.")
            return (kind, _hash(origin["mascot_sha256"]), _hash(origin["mask_sha256"]), item)
        if kind not in ("manifest", "asset", "generated"):
            raise ValueError("Unknown garment origin kind.")
        key = "png_sha256" if kind in ("manifest", "generated") else "source_sha256"
        return (kind, _hash(origin[key]))
    except KeyError as exc:
        raise ValueError(f"Garment origin is missing {exc.args[0]}.") from exc


def inventory_to_dict(inventory):
    return _thaw(
        {
            "schema_version": inventory.schema_version,
            "next_number": inventory.next_number,
            "garments": [
                {
                    **{
                        name: getattr(g, name)
                        for name in (
                            "number",
                            "name",
                            "style",
                            "style_choice",
                            "palette",
                            "palette_choice",
                            "ranges",
                            "version",
                            "origin",
                        )
                    },
                    "asset": {
                        "format": g.asset.format,
                        "files": [
                            {"role": f.role, "path": f.path, "sha256": f.sha256} for f in g.asset.files
                        ],
                    },
                }
                for g in inventory.garments
            ],
            "retired": list(inventory.retired),
            "defaults": dict(inventory.defaults),
            "active": inventory.active,
        }
    )


def inventory_from_dict(data):
    """Parse the versioned JSON value, refusing incomplete or structurally invalid records."""
    expected = {"schema_version", "next_number", "garments", "retired", "defaults", "active"}
    garment_fields = {
        "number",
        "name",
        "style",
        "style_choice",
        "palette",
        "palette_choice",
        "ranges",
        "asset",
        "version",
        "origin",
    }
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError("Inventory must contain the complete schema-1 fields.")
    if not isinstance(data["garments"], list) or not isinstance(data["retired"], list):
        raise ValueError("Inventory garments and retired numbers must be JSON arrays.")
    try:
        garments = []
        for record in data["garments"]:
            if not isinstance(record, dict) or set(record) != garment_fields:
                raise ValueError("Invalid or incomplete garment record.")
            asset = record["asset"]
            if (
                not isinstance(asset, dict)
                or set(asset) != {"format", "files"}
                or not isinstance(asset["files"], list)
            ):
                raise ValueError("Invalid asset record.")
            files = []
            for value in asset["files"]:
                if not isinstance(value, dict) or set(value) != {"role", "path", "sha256"}:
                    raise ValueError("Invalid asset file record.")
                files.append(AssetFile(**value))
            fields = dict(record)
            fields["asset"] = AssetRef(asset["format"], tuple(files))
            garments.append(Garment(**fields))
        return Inventory(**{**data, "garments": tuple(garments), "retired": tuple(data["retired"])})
    except (TypeError, KeyError, AttributeError) as exc:
        raise ValueError("Invalid inventory data.") from exc
