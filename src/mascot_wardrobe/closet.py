"""Identity-locked closet: one immutable mascot, an item inventory, named wardrobes and a catalog.

The mascot PNG and an explicit binary edit mask are copied byte-for-byte into the closet and
pinned by SHA-256. Every item is the same mascot with garment pixels inside the mask only; pixels
outside the mask are verified byte-identical to the source. Wardrobes pin each item's version and
hash, so editing an item or swapping a base asset is detected instead of silently reused.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from mascot_wardrobe.wardrobe import (
    CATEGORIES,
    LEGACY_RENDERER_CONFIG,
    LocalComposer,
    OutfitBrief,
    _safe_root,
)

SCHEMA_VERSION = 1
TILE = (192, 208)
GAP = 8
MAX_COLUMNS = 4
SHEET_BACKGROUND = (244, 244, 244)
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
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


def _palette(value):
    if (
        not isinstance(value, (tuple, list))
        or not 1 <= len(value) <= 6
        or any(not isinstance(c, str) or _COLOR.fullmatch(c) is None for c in value)
    ):
        raise ValueError("palette needs one to six #RRGGBB colors.")
    return tuple(value)


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
    def init(cls, root, mascot_png, edit_mask_png) -> Closet:
        """Create a closet from a user mascot and an explicit edit mask (both copied verbatim)."""
        if edit_mask_png is None:
            raise ValueError("An explicit edit mask PNG is required for the identity guarantee.")
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
        _write_atomic(root / "mascot" / "source.png", mascot_bytes)
        _write_atomic(root / "mask" / "edit-mask.png", mask_bytes)
        closet = cls(root, data)
        closet._save()
        return closet

    @classmethod
    def open(cls, root) -> Closet:
        root = _safe_root(Path(root))
        try:
            data = json.loads(_read_file(root / "closet.json", "closet.json").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("closet.json is not valid JSON.") from exc
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Unsupported closet schema; this version reads schema {SCHEMA_VERSION}.")
        closet = cls(root, data)
        closet.verify()
        return closet

    # -- paths and read access ----------------------------------------------------------------

    @property
    def source_path(self) -> Path:
        return self.root / "mascot" / "source.png"

    @property
    def mask_path(self) -> Path:
        return self.root / "mask" / "edit-mask.png"

    def item_path(self, item_id: str) -> Path:
        return self.root / "items" / f"{_name(item_id, 'item id')}.png"

    @staticmethod
    def _asset(item_id: str) -> str:
        """Canonical relative asset path; derived from the id, never taken from closet.json."""
        return f"items/{_name(item_id, 'item id')}.png"

    @property
    def active(self):
        return self._data["active"]

    def items(self) -> dict:
        return {i: _with_metadata(i, record) for i, record in self._data["items"].items()}

    def wardrobes(self) -> dict:
        return json.loads(json.dumps(self._data["wardrobes"]))

    # -- verification -------------------------------------------------------------------------

    def _load_identity(self):
        """Re-read the pinned mascot and mask from disk and prove they are the recorded ones."""
        mascot = _read_file(self.source_path, "The mascot source")
        if _sha256_bytes(mascot) != self._data["mascot"]["sha256"]:
            raise ValueError("The mascot source changed: sha256 mismatch with the closet record.")
        mask = _read_file(self.mask_path, "The edit mask")
        if _sha256_bytes(mask) != self._data["mask"]["sha256"]:
            raise ValueError("The edit mask changed: sha256 mismatch with the closet record.")
        if self._data["renderer"] != json.loads(json.dumps(LocalComposer.cache_config)):
            if self._data["renderer"] == LEGACY_RENDERER_CONFIG:
                raise ValueError(
                    "This closet was made by template set 2; upgrade it with: "
                    f"mascot-wardrobe closet --root {self.root} migrate"
                )
            raise ValueError("The renderer configuration changed; items were made by another template set.")
        source = _decode(mascot, "The mascot", "RGBA")
        edit, _ = _binary_mask(_decode(mask, "The edit mask", "L"))
        return source, edit

    def verify(self) -> None:
        """Raise ValueError unless mascot, mask and every item still match their records."""
        if not isinstance(self._data["items"], dict):
            raise ValueError("closet.json items must be an object.")
        for item_id, item in self._data["items"].items():
            _check_record_labels(item_id, item)
        source, mask = self._load_identity()
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

    def _render(self, source, mask, template, palette) -> bytes:
        if template not in CATEGORIES:
            raise ValueError(f"template must be one of: {', '.join(CATEGORIES)}.")
        width, height = source.size
        x0, y0, x1, y1 = self._data["mask"]["bbox"]
        # Half-pixel offsets keep int(v * n) on the mask bbox despite float rounding; the compositor
        # measures the silhouette inside the mask, so the mask is its region.
        box = ((x0 + 0.5) / width, (y0 + 0.5) / height, (x1 + 0.5) / width, (y1 + 0.5) / height)
        brief = OutfitBrief(template, palette, (), box, "closet item", region=mask)
        dressed = LocalComposer().render(source.copy(), brief)
        result = Image.composite(dressed, source, mask)  # mask 0 keeps every source byte
        if not _protected_matches(result, source, mask):
            raise ValueError("Rendered item alters protected pixels outside the edit mask.")
        return _png_bytes(result)

    def _store_item(self, item_id, template, palette, version, source, mask, name, description) -> dict:
        data = self._render(source, mask, template, palette)
        _write_atomic(self.item_path(item_id), data)
        record = {
            "name": name,
            "description": description,
            "template": template,
            "palette": list(palette),
            "version": version,
            "file": self._asset(item_id),
            "sha256": _sha256_bytes(data),
        }
        self._data["items"][item_id] = record
        self._save()
        return {"id": item_id, **record}

    def add_item(self, item_id, template, palette, *, name=None, description="") -> dict:
        _name(item_id, "item id")
        palette = _palette(palette)
        name = _fallback_name(item_id) if name is None else _display_name(name)
        description = _description(description)
        source, mask = self._load_identity()
        self.verify()
        if item_id in self._data["items"]:
            raise ValueError(f"Item {item_id} already exists; use edit.")
        return self._store_item(item_id, template, palette, 1, source, mask, name, description)

    def edit_item(self, item_id, *, template=None, palette=None, name=None, description=None) -> dict:
        """Change garment (new version, re-rendered) and/or labels (no version or pixel change)."""
        source, mask = self._load_identity()
        self.verify()
        current = _with_metadata(item_id, self._item(item_id))
        new_name = current["name"] if name is None else _display_name(name)
        new_description = current["description"] if description is None else _description(description)
        new_template = current["template"] if template is None else template
        new_palette = current["palette"] if palette is None else list(_palette(palette))
        garment_same = new_template == current["template"] and new_palette == current["palette"]
        labels_same = (new_name, new_description) == (current["name"], current["description"])
        if garment_same and labels_same:
            raise ValueError("Nothing to change: template, palette, name and description are identical.")
        if garment_same:  # labels only: the saved PNG, its hash and the version stay as they are
            record = self._data["items"][item_id]
            record["name"], record["description"] = new_name, new_description
            self._save()
            return {"id": item_id, **_with_metadata(item_id, record)}
        return self._store_item(
            item_id,
            new_template,
            tuple(new_palette),
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

    def _checked_wardrobe(self, name) -> dict:
        _name(name, "wardrobe name")
        self.verify()
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
