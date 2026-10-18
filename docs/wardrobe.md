# Wardrobe compositor

The `wardrobe` package turns one PNG and a body box into six outfits. It is deterministic,
local and non-semantic.

## What it does

- `WardrobeAgent.generate(mascot, profile, output_dir)` validates the request, renders six
  outfits with a `Renderer`, writes the assets and a `manifest.json`, and returns the manifest
  path. Calling it again with an identical request verifies and reuses the existing assets
  (no rewrite); a different request against the same output is refused rather than overwritten.
- The default renderer is `LocalComposer` (in `wardrobe/composer.py`): Pillow only, no
  diffusion, no remote service, no semantic inference.
- `Renderer` is an injectable protocol: `render(source, brief)` must return an RGBA image of the
  same size, and an optional JSON-serializable `cache_config` is bound into the manifest so a
  changed renderer configuration is not mistaken for a cache hit.

## Body box alignment

The mascot PNG keeps its original size. Clothing is painted only inside the normalized
`body_box` `(x1, y1, x2, y2)`, default `(0.22, 0.60, 0.78, 0.88)` of the image. Pixels outside
the box, including the head, are identical to the source. There is no face detection: you are
responsible for choosing a box that covers the body and not the face. Adjust it with
`--body-box` and check the six results.

The palette comes from the country profile. The default compositor uses only its first color
(garment) and last color (accent) for all six outfits; the other entries are validated and
recorded in the manifest but not painted. Garment names in a profile are descriptions for a
possible future provider; the default compositor uses a fixed per-category template (padded
coat, jacket, sweater, t-shirt, tank top, light strappy piece) and does not interpret the text.

Existing transparency is preserved. An opaque background is not removed and the overlay is
still limited to the body box. See the limitations in the README.

## Generated files

For each category the agent writes, under the output directory:

- `<id>-<cc>-<category>.png`: RGBA portrait at the source size.
- `<id>-<cc>-<category>.jpg`: 512x512 RGB on a white background.
- `<id>-<cc>-<category>-atlas.png`: RGBA state atlas, 1536x1872.
- `manifest.json`: `schema_version` 1, the request, the layout and the six outfits.

`<cc>` is the lowercase country code and the category appears with hyphens, for example
`sunny-br-very-cold.png`.

## Atlas layout

The atlas is a 9-row state atlas layout of 8 columns by 9 rows with 192x208 cells. The row order
is the exported contract:

1. idle
2. running-right
3. running-left
4. waving
5. jumping
6. failed
7. waiting
8. running
9. review

Each row has six usable frames; the last two columns are transparent. The motion is a
geometric translation of the portrait (small shifts, a hop for `jumping`, a drop for `failed`),
not an articulated animation. The manifest records the row order and geometry.
