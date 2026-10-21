# Identity-locked closet

`mascot-wardrobe closet` keeps **one** user-supplied mascot as an immutable source and dresses it
with reusable items. It never regenerates or redraws the character: the existing deterministic
`LocalComposer` paints garments, and only inside an explicit edit mask you provide. The classic
`generate` / `select` flow and its `manifest.json` are unchanged.

## Identity guarantee

- `closet init --mascot m.png --mask mask.png` copies both files **byte-for-byte** into the
  closet and records their SHA-256. The mask is required; there is no default and no detection.
- The mask is a PNG of the same size as the mascot, containing only `0` (protected) and `255`
  (editable). Anything else, an empty mask, or an editable area under 8x8 pixels is rejected.
- Each item is `composite(garment render, source, mask)`: protected pixels are taken from the
  source, then re-checked to be byte-identical (RGBA channels included) before the file is saved
  and again every time the closet is opened.
- The garment templates are fitted to the bounding box of the editable mask area. Pixels inside
  the box but outside the mask stay untouched.

## Layout and versioning

```
closet/
  closet.json          schema_version 1: hashes, items (with name/description), wardrobes, active
  mascot/source.png    the immutable mascot (verbatim copy)
  mask/edit-mask.png   the explicit edit mask (verbatim copy)
  items/<id>.png       saved item portraits (same size as the mascot)
  catalog/<name>.png   contact sheet; catalog/<name>.json lists its tiles
```

`closet.json` pins the mascot and mask hashes, the renderer `cache_config`, and per item its
template, palette, `version` and file hash. Every command re-hashes mascot, mask and all items
and fails with `changed ... sha256 mismatch` (exit code 2) if anything differs, so a swapped base
asset or hand-edited item cannot contaminate existing wardrobes. An unknown `schema_version` is
refused. The mascot of an existing closet cannot be replaced: start a new closet instead.

## Inventory

```
mascot-wardrobe closet --root DIR add coat --template very_cold --palette "#2878b5" "#e8b84b"
mascot-wardrobe closet --root DIR edit coat --palette "#aa2222"   # version 1 -> 2
mascot-wardrobe closet --root DIR remove coat
```

### Names and descriptions

The `id` is the stable slug used by wardrobes and file names and never changes. Each item also has an
editable human-readable `--name` (default: derived from the id, `rain-coat` -> `Rain Coat`) and a
free-text `--description` (default: empty):

```
mascot-wardrobe closet --root DIR add coat --template very_cold --palette "#2878b5" \
    --name "Winter Coat" --description "Wool, double lined."
mascot-wardrobe closet --root DIR edit coat --name "Parka"          # description untouched
mascot-wardrobe closet --root DIR edit coat --description ""        # clears the description
mascot-wardrobe closet --root DIR edit coat --description=-wool     # values starting with "-"
```

A value that begins with a dash must use the `--description=VALUE` (or `--name=VALUE`) form;
otherwise the argument parser reads it as an option.

- Limits: name up to 60 characters on one line; description up to 500 characters (newlines
  allowed; CRLF and CR are normalized to `\n`). Longer text or control characters are
  **rejected** with exit code 2, never truncated.
- `list`, the `add`/`edit` results and the catalog tile data carry `name` and `description`; they
  are stored in `closet.json`. Other JSON results (`init`, `save`, `use`, `remove`, `verify`) do not.
  Names and descriptions may contain Unicode separator spaces such as NBSP and U+3000; control and
  format characters and line breaks are rejected (descriptions may still contain `\n`).
- Editing only name/description does not re-render: the saved item PNG, its hash, its `version`,
  the mascot/mask hashes and all wardrobes are unchanged, so wardrobes do not become stale. The item
  and mascot image hashes never cover label text. Rebuild the catalog to see new text. Editing
  template/palette bumps the version as before and keeps the labels.
- Schema 1 is kept. Closets written before labels existed have no such keys; they are read with the
  id-derived name (repeated hyphens collapse: `a--b` -> `A B`) and an empty description, and the
  keys are written the next time the item is edited.
- Labels read from `closet.json` are validated like input. If `name`/`description` keys exist they
  must be strings within the limits above (name non-empty, one line, no control characters; only
  newlines allowed in descriptions), and every item record must be an object. Anything else
  (`null`, wrong type, over-long, control characters) fails `open`, `list`, `catalog` and the other
  commands with `error: ...` and exit code 2.

Templates are the six deterministic outfits (`very_cold` ... `very_hot`); the first palette
color is the garment and the last is the accent. Item ids and wardrobe names are lowercase slugs
(up to 40 characters). Removing an item that a wardrobe uses is refused and names the wardrobe.

## Named wardrobes

```
mascot-wardrobe closet --root DIR save winter --items coat tee   # create or replace (revision +1)
mascot-wardrobe closet --root DIR use winter                     # switch the active wardrobe
mascot-wardrobe closet --root DIR list
```

A wardrobe pins each item's `version` and `sha256`. If an item is edited afterwards, `use` and
`catalog` refuse the wardrobe as **stale** until you `save` it again; it is never silently
updated.

## Catalog sheet

```
mascot-wardrobe closet --root DIR catalog [--wardrobe winter]
```

Each tile shows the item's name (bold-ish) and its full description in wrapped text **below** the
mascot image, never over it. Each row grows to fit its longest label, so nothing is clipped; the
length limits above are what keep sheets bounded. Text uses Pillow's built-in font, so glyphs it
lacks (some non-Latin scripts) render as placeholder boxes.

Builds `catalog/<name>.png` (up to four 192x208 tiles per row, light grey background) by loading
the verified saved item files, not by re-rendering. Output is deterministic for the same assets
and Pillow version.

The JSON sidecar `catalog/<name>.json` has, at the top level: `schema_version`, `wardrobe`,
`revision`, `mascot_sha256`, `sheet` (relative path), `sheet_sha256`, `sheet_size` and `tiles`.
Each tile has:

| field | meaning |
| --- | --- |
| `item` | item id |
| `name`, `description` | the labels drawn on the sheet |
| `version` | item version pinned by the wardrobe |
| `asset`, `sha256` | relative path and hash of the saved item PNG |
| `mascot_sha256` | the shared mascot hash |
| `position` | `[x, y]` of the portrait on the sheet |
| `text_box` | `[left, top, right, bottom]` of the label region, directly below the tile image |
| `text_lines` | the wrapped name lines followed by the description lines, as drawn |

Two different fingerprints are involved, on purpose:

- The wardrobe `revision`, and each tile's `version` / `sha256`, track the wardrobe and the item
  images. They change only when the wardrobe is re-saved or an item's garment changes.
- `sheet_sha256` is the exact fingerprint of the rendered sheet. It also changes when label text
  changes, even though `revision`, `version` and every item hash stay the same.

## Not in this MVP

- No automatic mask or body detection; you author the mask.
- No import of externally generated garment art; items come from the six built-in templates.
- No per-category (weather) mapping of closet wardrobes, and no
  wiring into `select` / `publish` yet.
- Item writes are atomic per file, but `closet.json` and an item file are not updated in one
  transaction: a crash between them is reported by the next hash check, not repaired.
