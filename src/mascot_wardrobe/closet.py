"""Identity-locked closet: one immutable mascot, an item inventory, named wardrobes and a catalog.

The mascot PNG and an explicit binary edit mask are copied byte-for-byte into the closet and
pinned by SHA-256. Every item is the same mascot with garment pixels inside the mask only; pixels
outside the mask are verified byte-identical to the source. Wardrobes pin each item's version and
hash, so editing an item or swapping a base asset is detected instead of silently reused.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from mascot_wardrobe.wardrobe import (
    CATEGORIES,
    LocalComposer,
    OutfitBrief,
    _safe_root,
)
from mascot_wardrobe.wardrobe.composer import PRIOR_RENDERER_CONFIGS
from mascot_wardrobe.wardrobe.styles import (
    AUTO_PALETTES,
    resolve_palette,
    resolve_style,
    validate_provenance,
    validate_style,
)

SCHEMA_VERSION = 1
TILE = (192, 208)
GAP = 8
MAX_COLUMNS = 4
SHEET_BACKGROUND = (244, 244, 244)
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_ITEMS_DIR = re.compile(r"items(-t[0-9]+)?")
DEFAULT_ITEMS_DIR = "items"
DEFAULT_STYLE = "classic"
_MAX_PIXELS = 16_777_216
MAX_NAME_LENGTH = 60
MAX_DESCRIPTION_LENGTH = 500
TEXT_SIZE = 12
TEXT_COLOR = (32, 32, 32)
TEXT_PAD = 6


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _name(value, what):
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise ValueError(f"{what} must be a lowercase slug of up to 40 characters.")
    return value


def _is_plain_text(text):
    """Printable characters plus Unicode separator spaces (NBSP, U+3000); no control/format/line breaks."""
    return all(c.isprintable() or unicodedata.category(c) == "Zs" for c in text)


def _display_name(value):
    """A single-line, human-readable label; rejected (never truncated) when over the limit."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("name must be a non-empty string.")
    value = value.strip()
    if len(value) > MAX_NAME_LENGTH:
        raise ValueError(f"name is too long: {len(value)} characters, the limit is {MAX_NAME_LENGTH}.")
    if not _is_plain_text(value):
        raise ValueError("name must be a single line without control characters.")
    return value


def _normalize_newlines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _description(value):
    """Free text (newlines allowed); rejected (never truncated) when over the limit."""
    if not isinstance(value, str):
        raise ValueError("description must be a string.")
    value = _normalize_newlines(value.strip())
    if len(value) > MAX_DESCRIPTION_LENGTH:
        raise ValueError(
            f"description is too long: {len(value)} characters, the limit is {MAX_DESCRIPTION_LENGTH}."
        )
    if not _is_plain_text(value.replace("\n", "")):
        raise ValueError("description cannot contain control characters other than newlines.")
    return value


def _fallback_name(item_id):
    return " ".join(part for part in item_id.split("-") if part).title()


def _check_record_labels(item_id, record):
    """Persisted labels are untrusted: a missing key is a legacy record, a present one must be valid."""
    if not isinstance(record, dict):
        raise ValueError(f"Item {item_id} has a malformed record in closet.json.")
    try:
        if "name" in record:
            _display_name(record["name"])
        if "description" in record:
            _description(record["description"])
    except ValueError as exc:
        raise ValueError(f"Item {item_id} has an invalid label in closet.json: {exc}") from exc


def _with_metadata(item_id, record):
    """Schema-1 records written before labels existed read as id-derived name and empty description."""
    record = json.loads(json.dumps(record))
    record.setdefault("name", _fallback_name(item_id))
    record.setdefault("description", "")
    record.setdefault("style", DEFAULT_STYLE)
    record.setdefault("style_choice", "default")
    record.setdefault("palette_choice", "default")
    record["description"] = _normalize_newlines(record["description"])  # hand-edited CRLF; file untouched
    return record


def _font():
    return ImageFont.load_default(TEXT_SIZE)


def _wrap(text, font, width):
    """Wrap on whitespace, hard-breaking words wider than the tile; never drops characters."""
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            while font.getlength(word) > width:
                if line:
                    lines.append(line)
                    line = ""
                cut = max(
                    1, next((i for i in range(1, len(word) + 1) if font.getlength(word[:i]) > width), 2) - 1
                )
                lines.append(word[:cut])
                word = word[cut:]
            trial = f"{line} {word}" if line else word
            if line and font.getlength(trial) > width:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
    return lines


def _items_dir(value):
    """closet.json is untrusted: the items directory is one of a fixed family of names."""
    if not isinstance(value, str) or _ITEMS_DIR.fullmatch(value) is None:
        raise ValueError("closet.json items_dir must be 'items' or 'items-t<number>'.")
    return value


def _check_record_choices(item_id, record):
    """A missing style key is a record without choices; a present one must be valid."""
    try:
        if "style" in record:
            validate_style(record["style"])
        for key in ("style_choice", "palette_choice"):
            if key in record:
                validate_provenance(record[key])
    except ValueError as exc:
        raise ValueError(
            f"Item {item_id} has an invalid style or choice source in closet.json: {exc}"
        ) from exc


def _read_file(path: Path, what: str) -> bytes:
    if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != path.anchor):
        raise ValueError(f"{what} cannot be reached through a symbolic link.")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{what} is missing or unreadable.") from exc


def _decode(data: bytes, what: str, mode: str) -> Image.Image:
    import io

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                raise ValueError(f"{what} must be a static PNG.")
            if image.width * image.height > _MAX_PIXELS:
                raise ValueError(f"{what} exceeds the 16 megapixel limit.")
            image.load()
            return image.convert(mode)
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError(f"{what} is not a valid PNG.") from exc


def _binary_mask(mask: Image.Image) -> tuple[Image.Image, tuple[int, int, int, int]]:
    histogram = mask.histogram()
    if sum(histogram[1:255]):
        raise ValueError("The edit mask must be binary: only 0 (protected) and 255 (editable).")
    box = mask.getbbox()
    if box is None:
        raise ValueError("The edit mask has no editable pixels.")
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        raise ValueError("The editable mask area must span at least 8x8 pixels.")
    return mask, box


def _protected_matches(image: Image.Image, source: Image.Image, mask: Image.Image) -> bool:
    blank = Image.new("RGBA", source.size)
    inverse = mask.point(lambda v: 255 if v == 0 else 0)
    return (
        Image.composite(image, blank, inverse).tobytes() == Image.composite(source, blank, inverse).tobytes()
    )


def _check_body_box(box) -> list[float]:
    """Return a normalized ``[x0, y0, x1, y1]`` fit box or raise ValueError."""
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError("body_box must be four numbers: X1 Y1 X2 Y2.")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in box):
        raise ValueError("body_box values must be numbers.")
    x0, y0, x1, y1 = (float(v) for v in box)
    if not all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in (x0, y0, x1, y1)):
        raise ValueError("body_box values must be finite and within 0..1.")
    if not (x0 < x1 and y0 < y1):
        raise ValueError("body_box must be ordered: X1 < X2 and Y1 < Y2.")
    return [x0, y0, x1, y1]


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tmp-", suffix=".part", delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _png_bytes(image: Image.Image) -> bytes:
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class Closet:
    """A directory holding the pinned mascot, edit mask, items, wardrobes and catalog sheets."""

    def __init__(self, root: Path, data: dict):
        self.root = root
        self._data = data

    # -- construction -------------------------------------------------------------------------

    @classmethod
    def init(cls, root, mascot_png, edit_mask_png, *, body_box=None) -> Closet:
        """Create a closet from a user mascot and an explicit edit mask (both copied verbatim).

        ``body_box`` optionally stores a normalized garment fit box, independent of the edit mask.
        """
        if edit_mask_png is None:
            raise ValueError("An explicit edit mask PNG is required for the identity guarantee.")
        stored_box = None if body_box is None else _check_body_box(body_box)
        root = _safe_root(Path(root))
        if (root / "closet.json").exists():
            raise ValueError("A closet already exists here; the mascot source is immutable.")
        mascot_bytes = _read_file(Path(mascot_png), "The mascot PNG")
        mask_bytes = _read_file(Path(edit_mask_png), "The edit mask PNG")
        source = _decode(mascot_bytes, "The mascot", "RGBA")
        mask, box = _binary_mask(_decode(mask_bytes, "The edit mask", "L"))
        if mask.size != source.size:
            raise ValueError("The edit mask must have the same size as the mascot.")
        data = {
            "schema_version": SCHEMA_VERSION,
            "mascot": {"sha256": _sha256_bytes(mascot_bytes), "size": list(source.size)},
            "mask": {"sha256": _sha256_bytes(mask_bytes), "bbox": list(box)},
            "renderer": json.loads(json.dumps(LocalComposer.cache_config)),
            "items": {},
            "wardrobes": {},
            "active": None,
        }
        if stored_box is not None:
            data["body_box"] = stored_box
        _write_atomic(root / "mascot" / "source.png", mascot_bytes)
        _write_atomic(root / "mask" / "edit-mask.png", mask_bytes)
        closet = cls(root, data)
        closet._save()
        return closet

    @classmethod
    def open(cls, root, *, renderer_check=True) -> Closet:
        """Open with integrity checks; read-only consumers may skip renderer compatibility only.

        ``renderer_check=False`` never migrates assets or bypasses hashes/protected pixels.
        Rendering and mutation methods still require the current renderer.
        """
        root = _safe_root(Path(root))
        try:
            data = json.loads(_read_file(root / "closet.json", "closet.json").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("closet.json is not valid JSON.") from exc
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Unsupported closet schema; this version reads schema {SCHEMA_VERSION}.")
        closet = cls(root, data)
        closet.verify(renderer_check=renderer_check)
        return closet

    @classmethod
    def migrate(cls, root, *, dry_run=False) -> dict:
        """Upgrade a template-set-2, -3, or -4 closet to current; ``closet.json`` is the commit point.

        Revision 2 items become ``classic``; revisions 3 and 4 keep style and choice provenance. New
        portraits go to ``items-t5/``; old items and catalogs are untouched. The original JSON is
        kept under ``backups/``. Nothing is written when the plan is refused or ``dry_run`` is set.
        """
        root = _safe_root(Path(root))
        original = _read_file(root / "closet.json", "closet.json")
        try:
            data = json.loads(original.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("closet.json is not valid JSON.") from exc
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Unsupported closet schema; this version reads schema {SCHEMA_VERSION}.")
        current = json.loads(json.dumps(LocalComposer.cache_config))
        if data.get("renderer") == current:
            return {"migrated": False, "reason": "already current"}
        prior = data.get("renderer")
        if prior not in PRIOR_RENDERER_CONFIGS:
            raise ValueError("Cannot migrate: the closet renderer configuration is not a known legacy one.")
        supported_dirs = {
            2: (DEFAULT_ITEMS_DIR,),
            3: (DEFAULT_ITEMS_DIR, "items-t3"),
            4: (DEFAULT_ITEMS_DIR, "items-t4"),
        }[prior["templates"]]
        if data.get("items_dir", DEFAULT_ITEMS_DIR) not in supported_dirs:
            raise ValueError("Cannot migrate: the prior renderer has an unexpected items directory.")
        legacy = cls(root, data)
        legacy.verify(renderer_check=False)
        wardrobes = data.get("wardrobes")
        if not isinstance(wardrobes, dict):
            raise ValueError("closet.json wardrobes must be an object.")
        for name, wardrobe in wardrobes.items():
            _name(name, "wardrobe name")
            if not isinstance(wardrobe, dict) or not isinstance(wardrobe.get("items"), list):
                raise ValueError(f"Wardrobe {name} is malformed.")
            for entry in wardrobe["items"]:
                item = data["items"].get(entry.get("id")) if isinstance(entry, dict) else None
                if item is None or (item["version"], item["sha256"]) != (
                    entry.get("version"),
                    entry.get("sha256"),
                ):
                    raise ValueError(f"Wardrobe {name} does not match its items; fix it before migrating.")
        catalog = root / "catalog"
        stale = sorted(f"catalog/{p.name}" for p in catalog.glob("*.png")) if catalog.is_dir() else []
        plan = {"items": sorted(data["items"]), "wardrobes": sorted(wardrobes), "stale_catalogs": stale}
        if dry_run:
            return {"migrated": False, "dry_run": True, **plan}
        new_dir = f"items-t{current['templates']}"
        _items_dir(new_dir)
        if (root / new_dir).is_symlink():
            raise ValueError(f"{new_dir} cannot be a symbolic link.")
        backups = root / "backups"
        number = 1
        while (backups / f"closet-templates-{prior['templates']}-{number}.json").exists():
            number += 1
        backup = backups / f"closet-templates-{prior['templates']}-{number}.json"
        _write_atomic(backup, original)
        if _sha256_bytes(_read_file(backup, "The backup")) != _sha256_bytes(original):
            raise ValueError("The backup does not match closet.json; migration stopped.")
        source, mask = legacy._load_identity(renderer_check=False)
        old_hash = _sha256_bytes(original)
        new = json.loads(json.dumps(data))
        new["renderer"] = current
        new["items_dir"] = new_dir
        hashes = {}
        for item_id, record in data["items"].items():
            meta = _with_metadata(item_id, record)
            style = DEFAULT_STYLE if prior["templates"] == 2 else meta["style"]
            rendered = legacy._render(source, mask, meta["template"], tuple(meta["palette"]), style)
            _write_atomic(root / new_dir / f"{_name(item_id, 'item id')}.png", rendered)
            hashes[item_id] = _sha256_bytes(rendered)
            new["items"][item_id] = {
                "name": meta["name"],
                "description": meta["description"],
                "template": meta["template"],
                "style": style,
                "style_choice": "migrated" if prior["templates"] == 2 else meta["style_choice"],
                "palette": list(meta["palette"]),
                "palette_choice": "migrated" if prior["templates"] == 2 else meta["palette_choice"],
                "version": meta["version"],
                "file": f"{new_dir}/{item_id}.png",
                "sha256": hashes[item_id],
                "migrated_from": {"renderer": prior, "sha256": record["sha256"]},
            }
        for wardrobe in new["wardrobes"].values():
            wardrobe["revision"] += 1
            for entry in wardrobe["items"]:
                entry["sha256"] = hashes[entry["id"]]
        cls(root, new).verify()  # the in-memory state against the files already written
        _write_atomic(root / "closet.json", json.dumps(new, indent=2, sort_keys=True).encode())
        try:
            cls.open(root)
        except Exception as exc:
            _write_atomic(root / "closet.json", original)
            raise ValueError(f"Migration failed verification and was rolled back: {exc}") from exc
        return {"migrated": True, "backup": str(backup), "backup_sha256": old_hash, **plan}

    # -- paths and read access ----------------------------------------------------------------

    @property
    def mascot_sha256(self) -> str:
        """The pinned mascot identity hash (read-only)."""
        return self._data["mascot"]["sha256"]

    @property
    def mask_sha256(self) -> str:
        """The pinned edit-mask identity hash (read-only)."""
        return self._data["mask"]["sha256"]

    @property
    def source_path(self) -> Path:
        return self.root / "mascot" / "source.png"

    @property
    def mask_path(self) -> Path:
        return self.root / "mask" / "edit-mask.png"

    def item_path(self, item_id: str) -> Path:
        return self.root / self._items_dir / f"{_name(item_id, 'item id')}.png"

    @property
    def _items_dir(self) -> str:
        return _items_dir(self._data.get("items_dir", DEFAULT_ITEMS_DIR))

    def _asset(self, item_id: str) -> str:
        """Canonical relative asset path; derived from the id, never taken from closet.json."""
        return f"{self._items_dir}/{_name(item_id, 'item id')}.png"

    @property
    def active(self):
        return self._data["active"]

    def items(self) -> dict:
        return {i: _with_metadata(i, record) for i, record in self._data["items"].items()}

    def wardrobes(self) -> dict:
        return json.loads(json.dumps(self._data["wardrobes"]))

    # -- verification -------------------------------------------------------------------------

    def _load_identity(self, *, renderer_check=True):
        """Re-read the pinned mascot and mask from disk and prove they are the recorded ones."""
        mascot = _read_file(self.source_path, "The mascot source")
        if _sha256_bytes(mascot) != self._data["mascot"]["sha256"]:
            raise ValueError("The mascot source changed: sha256 mismatch with the closet record.")
        mask = _read_file(self.mask_path, "The edit mask")
        if _sha256_bytes(mask) != self._data["mask"]["sha256"]:
            raise ValueError("The edit mask changed: sha256 mismatch with the closet record.")
        if renderer_check and self._data["renderer"] != json.loads(json.dumps(LocalComposer.cache_config)):
            if self._data["renderer"] in PRIOR_RENDERER_CONFIGS:
                raise ValueError(
                    f"This closet was made by template set {self._data['renderer']['templates']}; "
                    "upgrade it with: "
                    f"mascot-wardrobe closet --root {self.root} migrate"
                )
            raise ValueError("The renderer configuration changed; items were made by another template set.")
        source = _decode(mascot, "The mascot", "RGBA")
        edit, _ = _binary_mask(_decode(mask, "The edit mask", "L"))
        return source, edit

    def verify(self, *, renderer_check=True) -> None:
        """Raise ValueError unless mascot, mask and every item still match their records."""
        if not isinstance(self._data["items"], dict):
            raise ValueError("closet.json items must be an object.")
        if "body_box" in self._data:
            _check_body_box(self._data["body_box"])
        _items_dir(self._data.get("items_dir", DEFAULT_ITEMS_DIR))  # before any path is built
        for item_id, item in self._data["items"].items():
            _check_record_labels(item_id, item)
            _check_record_choices(item_id, item)
        source, mask = self._load_identity(renderer_check=renderer_check)
        for item_id, item in self._data["items"].items():
            if "file" in item and item["file"] != self._asset(item_id):
                raise ValueError(
                    f"Item {item_id} file must be {self._asset(item_id)!r}, not the recorded path."
                )
            data = _read_file(self.item_path(item_id), f"Item {item_id}")
            if _sha256_bytes(data) != item["sha256"]:
                raise ValueError(f"Item {item_id} changed: sha256 mismatch with the closet record.")
            image = _decode(data, f"Item {item_id}", "RGBA")
            if image.size != source.size or not _protected_matches(image, source, mask):
                raise ValueError(f"Item {item_id} alters protected pixels outside the edit mask.")

    # -- inventory ----------------------------------------------------------------------------

    def _render(self, source, mask, template, palette, style) -> bytes:
        if template not in CATEGORIES:
            raise ValueError(f"template must be one of: {', '.join(CATEGORIES)}.")
        width, height = source.size
        if "body_box" in self._data:
            # An explicit fit box is independent of the (possibly wider) paint/edit mask.
            box = tuple(self._data["body_box"])
        else:
            x0, y0, x1, y1 = self._data["mask"]["bbox"]
            # Half-pixel offsets keep int(v * n) on the mask bbox despite float rounding; the
            # compositor measures the silhouette inside the mask, so the mask is its region. A bbox
            # reaching the image edge is clamped to 1.0, which int(v * n) maps back to the edge exactly.
            box = tuple(
                min(1.0, (v + 0.5) / n)
                for v, n in zip((x0, y0, x1, y1), (width, height, width, height), strict=True)
            )
        brief = OutfitBrief(template, palette, (), box, "closet item", style=style, region=mask)
        dressed = LocalComposer().render(source.copy(), brief)
        result = Image.composite(dressed, source, mask)  # mask 0 keeps every source byte
        if not _protected_matches(result, source, mask):
            raise ValueError("Rendered item alters protected pixels outside the edit mask.")
        return _png_bytes(result)

    def _store_item(
        self,
        item_id,
        template,
        palette,
        style,
        style_choice,
        palette_choice,
        version,
        source,
        mask,
        name,
        description,
    ) -> dict:
        data = self._render(source, mask, template, palette, style)
        _write_atomic(self.item_path(item_id), data)
        record = {
            "name": name,
            "description": description,
            "template": template,
            "style": style,
            "style_choice": style_choice,
            "palette": list(palette),
            "palette_choice": palette_choice,
            "version": version,
            "file": self._asset(item_id),
            "sha256": _sha256_bytes(data),
        }
        self._data["items"][item_id] = record
        self._save()
        return {"id": item_id, **record}

    def _seed(self, item_id) -> str:
        return self._data["mascot"]["sha256"] + item_id

    def add_item(self, item_id, template, palette=None, *, style=None, name=None, description="") -> dict:
        """Add an item. ``None`` means the library default (classic, first curated palette);
        ``"auto"`` resolves deterministically from the mascot hash and the item id."""
        _name(item_id, "item id")
        if template not in CATEGORIES:
            raise ValueError(f"template must be one of: {', '.join(CATEGORIES)}.")
        if style is None:
            style, style_choice = DEFAULT_STYLE, "default"
        else:
            style, style_choice = resolve_style(style, template, self._seed(item_id))
        if palette is None:
            palette, palette_choice = AUTO_PALETTES[DEFAULT_STYLE][0], "default"
        else:
            palette, palette_choice = resolve_palette(palette, style, template, self._seed(item_id))
        name = _fallback_name(item_id) if name is None else _display_name(name)
        description = _description(description)
        source, mask = self._load_identity()
        self.verify()
        if item_id in self._data["items"]:
            raise ValueError(f"Item {item_id} already exists; use edit.")
        return self._store_item(
            item_id,
            template,
            palette,
            style,
            style_choice,
            palette_choice,
            1,
            source,
            mask,
            name,
            description,
        )

    def edit_item(
        self, item_id, *, template=None, style=None, palette=None, name=None, description=None
    ) -> dict:
        """Change garment (new version, re-rendered), choice sources (record only) and/or labels.

        ``None`` keeps the stored value, even when the template changes; only an explicit ``"auto"``
        re-resolves, using the new template and style.
        """
        source, mask = self._load_identity()
        self.verify()
        current = _with_metadata(item_id, self._item(item_id))
        new_name = current["name"] if name is None else _display_name(name)
        new_description = current["description"] if description is None else _description(description)
        new_template = current["template"] if template is None else template
        if new_template not in CATEGORIES:
            raise ValueError(f"template must be one of: {', '.join(CATEGORIES)}.")
        seed = self._seed(item_id)
        new_style, style_choice = current["style"], current["style_choice"]
        if style is not None:
            new_style, style_choice = resolve_style(style, new_template, seed)
        new_palette, palette_choice = current["palette"], current["palette_choice"]
        if palette is not None:
            resolved, palette_choice = resolve_palette(palette, new_style, new_template, seed)
            new_palette = list(resolved)
        garment_same = (new_template, new_style, new_palette) == (
            current["template"],
            current["style"],
            current["palette"],
        )
        choices_same = (style_choice, palette_choice) == (current["style_choice"], current["palette_choice"])
        labels_same = (new_name, new_description) == (current["name"], current["description"])
        if garment_same and choices_same and labels_same:
            raise ValueError(
                "Nothing to change: template, style, palette, name and description are identical."
            )
        if garment_same:  # metadata only: the saved PNG, its hash and the version stay as they are
            record = self._data["items"][item_id]
            record["name"], record["description"] = new_name, new_description
            record["style"], record["palette"] = new_style, list(new_palette)
            record["style_choice"], record["palette_choice"] = style_choice, palette_choice
            self._save()
            return {"id": item_id, **_with_metadata(item_id, record)}
        return self._store_item(
            item_id,
            new_template,
            tuple(new_palette),
            new_style,
            style_choice,
            palette_choice,
            current["version"] + 1,
            source,
            mask,
            new_name,
            new_description,
        )

    def remove_item(self, item_id) -> None:
        self.verify()
        self._item(item_id)
        users = sorted(
            name
            for name, wardrobe in self._data["wardrobes"].items()
            if any(entry["id"] == item_id for entry in wardrobe["items"])
        )
        if users:
            raise ValueError(f"Item {item_id} is used by wardrobe(s): {', '.join(users)}; save them first.")
        del self._data["items"][item_id]
        self._save()
        self.item_path(item_id).unlink(missing_ok=True)

    def _item(self, item_id) -> dict:
        _name(item_id, "item id")
        try:
            return self._data["items"][item_id]
        except KeyError:
            raise ValueError(f"Unknown item {item_id}.") from None

    # -- wardrobes ----------------------------------------------------------------------------

    def save_wardrobe(self, name, item_ids) -> dict:
        """Create or replace a named wardrobe, pinning each item's current version and hash."""
        _name(name, "wardrobe name")
        self.verify()
        if not isinstance(item_ids, (list, tuple)) or not item_ids:
            raise ValueError("A wardrobe needs at least one item.")
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("A wardrobe cannot list an item twice.")
        entries = [
            {"id": i, "version": self._item(i)["version"], "sha256": self._item(i)["sha256"]}
            for i in item_ids
        ]
        previous = self._data["wardrobes"].get(name)
        self._data["wardrobes"][name] = {
            "revision": previous["revision"] + 1 if previous else 1,
            "items": entries,
        }
        self._save()
        return {"name": name, **self.wardrobes()[name]}

    def _checked_wardrobe(self, name, *, renderer_check=True) -> dict:
        _name(name, "wardrobe name")
        self.verify(renderer_check=renderer_check)
        try:
            wardrobe = self._data["wardrobes"][name]
        except KeyError:
            raise ValueError(f"Unknown wardrobe {name}.") from None
        for entry in wardrobe["items"]:
            item = self._data["items"].get(entry["id"])
            if item is None or (item["version"], item["sha256"]) != (entry["version"], entry["sha256"]):
                raise ValueError(
                    f"Wardrobe {name} is stale: item {entry['id']} changed since it was saved; re-save it."
                )
        return wardrobe

    def wardrobe_entries(self, name, *, renderer_check=True) -> list:
        """Read-only copy of a verified, non-stale wardrobe's entries; never changes ``active``."""
        return json.loads(json.dumps(self._checked_wardrobe(name, renderer_check=renderer_check)["items"]))

    def remove_wardrobe(self, name) -> None:
        """Remove only a non-active named entry; retain items, assets, catalogs and inventory."""
        _name(name, "wardrobe name")
        self.verify()
        if name not in self._data["wardrobes"]:
            raise ValueError(f"Unknown wardrobe {name}.")
        if self.active == name:
            raise ValueError(f"Cannot remove active wardrobe {name}.")
        data = json.loads(json.dumps(self._data))
        del data["wardrobes"][name]
        _write_atomic(self.root / "closet.json", json.dumps(data, indent=2, sort_keys=True).encode())
        self._data = data

    def use_wardrobe(self, name) -> dict:
        wardrobe = self._checked_wardrobe(name)
        self._data["active"] = name
        self._save()
        return {"name": name, **json.loads(json.dumps(wardrobe))}

    # -- catalog ------------------------------------------------------------------------------

    def build_catalog(self, name=None) -> dict:
        """Compose a contact sheet from the saved item PNGs of a wardrobe (default: the active one)."""
        self.verify()
        name = self._data["active"] if name is None else name
        if name is None:
            raise ValueError("No active wardrobe; pass a wardrobe name or switch to one first.")
        wardrobe = self._checked_wardrobe(name)
        mascot_sha = self._data["mascot"]["sha256"]
        count = len(wardrobe["items"])
        columns = min(count, MAX_COLUMNS)
        rows = -(-count // columns)
        font = _font()
        line_height = font.getbbox("Hg")[3] + 3
        text_width = TILE[0] - 2 * TEXT_PAD
        labels = []
        for entry in wardrobe["items"]:
            meta = _with_metadata(entry["id"], self._data["items"][entry["id"]])
            labels.append(
                (
                    meta,
                    _wrap(meta["name"], font, text_width),
                    _wrap(meta["description"], font, text_width) if meta["description"] else [],
                )
            )
        row_text = [  # each row is as tall as its longest label, so nothing is clipped
            max(
                (len(n) + len(d)) * line_height + TEXT_PAD
                for _, n, d in labels[r * columns : (r + 1) * columns]
            )
            for r in range(rows)
        ]
        row_top = [GAP]
        for height in row_text[:-1]:
            row_top.append(row_top[-1] + TILE[1] + height + GAP)
        size = (columns * (TILE[0] + GAP) + GAP, row_top[-1] + TILE[1] + row_text[-1] + GAP)
        sheet = Image.new("RGB", size, SHEET_BACKGROUND)
        draw = ImageDraw.Draw(sheet)
        tiles = []
        for index, entry in enumerate(wardrobe["items"]):
            asset = self._asset(entry["id"])
            saved = _read_file(self.item_path(entry["id"]), f"Item {entry['id']}")
            if _sha256_bytes(saved) != entry["sha256"]:
                raise ValueError(f"Item {entry['id']} changed: sha256 mismatch with the closet record.")
            # bytes were read once and hash-checked; never reopen the path
            portrait = _decode(saved, f"Item {entry['id']}", "RGBA")  # the verified bytes, not a re-render
            portrait.thumbnail(TILE, Image.Resampling.LANCZOS)
            left = GAP + (index % columns) * (TILE[0] + GAP)
            top = row_top[index // columns]
            x = left + (TILE[0] - portrait.width) // 2
            y = top + (TILE[1] - portrait.height) // 2
            sheet.paste(portrait, (x, y), portrait)
            meta, name_lines, description_lines = labels[index]
            text_top = top + TILE[1] + TEXT_PAD // 2  # strictly below the image area
            ty = text_top
            for line in name_lines:
                draw.text((left + TEXT_PAD, ty), line, fill=TEXT_COLOR, font=font)
                draw.text((left + TEXT_PAD + 1, ty), line, fill=TEXT_COLOR, font=font)  # faux bold
                ty += line_height
            for line in description_lines:
                draw.text((left + TEXT_PAD, ty), line, fill=TEXT_COLOR, font=font)
                ty += line_height
            tiles.append(
                {
                    "item": entry["id"],
                    "name": meta["name"],
                    "description": meta["description"],
                    "version": entry["version"],
                    "asset": asset,
                    "sha256": entry["sha256"],
                    "mascot_sha256": mascot_sha,
                    "position": [x, y],
                    "text_box": [
                        left,
                        top + TILE[1],
                        left + TILE[0],
                        top + TILE[1] + row_text[index // columns],
                    ],
                    "text_lines": name_lines + description_lines,
                }
            )
        data = _png_bytes(sheet)
        sheet_path = self.root / "catalog" / f"{name}.png"
        manifest_path = self.root / "catalog" / f"{name}.json"
        record = {
            "schema_version": SCHEMA_VERSION,
            "wardrobe": name,
            "revision": wardrobe["revision"],
            "mascot_sha256": mascot_sha,
            "sheet": f"catalog/{name}.png",
            "sheet_sha256": _sha256_bytes(data),
            "sheet_size": list(size),
            "tiles": tiles,
        }
        _write_atomic(sheet_path, data)
        _write_atomic(manifest_path, json.dumps(record, indent=2).encode("utf-8"))
        return {**record, "sheet": str(sheet_path), "manifest": str(manifest_path)}

    # -- persistence --------------------------------------------------------------------------

    def _save(self) -> None:
        _write_atomic(self.root / "closet.json", json.dumps(self._data, indent=2, sort_keys=True).encode())
