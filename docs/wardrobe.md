# Wardrobe compositor

The `wardrobe` package turns one PNG and a body box into six outfits. It is deterministic,
local and non-semantic.

For a hash-pinned mascot, an explicit edit mask, item inventory and named wardrobes, see
[closet.md](closet.md).

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

The example mascot has arm nubs and a head that overlaps its body, so the README quickstart,
`scripts/e2e-smoke.sh` and the demo images all pass the same explicit box
`0.125 0.555 0.875 0.915` (from just under the head to the hem, including the arms). The global
default stays unchanged.

## Style and palette

`generate` takes two choices, `--style` and `--palette`.

- Styles: `classic` (plain fitted garments) and `streetwear` (fitted geometric layers: panels,
  stripes and prints). Styles are generic geometric details drawn by the local compositor; no
  external reference images are sent or fetched, and nothing is copied from a brand or an
  existing design. Each streetwear template has its own colour-block construction: contrast
  sleeves and a highlight skirt (very_cold), side panels and a zip placket (cold), a deep lower
  block and pocket (mild), a yoke and sleeves (ambient), a two-tone split (warm) and a light body
  with a dark hem block (very_hot). The seams, stripes, rib hems and chest bars stay on every one.
- Palette: one to six `#RRGGBB` colours with four roles. `base` is the first colour, `accent` the
  last, `secondary` is the second colour when there are three or more (otherwise the base) and
  `highlight` is the second to last when there are four or more (otherwise the accent). A
  one-colour palette makes all four roles equal; streetwear panels and prints are then invisible.
  For example the four colours `#101014 #4920A8 #9B4DFF #DDC5FF` map to base, secondary, accent
  and highlight in that order (an example only, not a built-in default).
- `auto` for either option lets the generator choose from a small curated table. It is
  deterministic and local: the result depends only on the mascot hash and the country (for
  `generate`), so repeating a run in any process gives the same values. No randomness, clock,
  network or model is used.
- Interactive and non-interactive: on a terminal, a missing `--style` or `--palette` is asked
  with a prompt (choose a listed option, supply your own, or let the generator choose; invalid
  answers are re-asked up to three times, end of input is an error). Prompts go to stderr, stdout
  stays JSON. When input is not a terminal, or with the global `--no-input` flag, nothing is
  asked: a missing value exits with code 2 and an `error:` line naming the flag, and nothing is
  written. Pass explicit values in scripts, for example `--style classic --palette auto`.
- Provenance: the manifest `request` records `style`, `palette`, `style_choice` and
  `palette_choice`. Values are `user` (passed explicitly or typed at the prompt), `auto`
  (generator choice), `default` (library default of `classic`) and `profile` (the country
  profile palette used by library callers that pass no palette). Closet items additionally use
  `migrated` (see [closet.md](closet.md)). Repeating a run with the same values is a no-op; a
  different style or palette against the same output is refused; a change of provenance alone
  rewrites only the manifest.

Library callers keep the old behaviour: `prepare_wardrobe` without `style`/`palette` renders
`classic` with the country profile palette.

## Fit

The six templates are fitted to the silhouette that is actually in the image, not to fixed
fractions of the box. `measure_fit` reads the alpha channel inside the body box (inside the closet
edit mask, when there is one) and finds the neck row, the neck width, the shoulder row, the torso
outline per row and the hem row. Garments are then built as layers (base, panels, details) that
are clipped to that coverage, so no paint lands on a transparent pixel, outside the mask or above
the neck row. The collar meets the head or chin line without painting over it, off-centre bodies
are followed by the measured centre rather than the box centre, long sleeves (very cold, cold,
mild) cover the arm nubs down to a cuff, the t-shirt covers the upper arm, and the sleeveless tee
and the camisole leave the arms bare. A box whose top is below the head simply starts the garment
at the first silhouette row.

There is still no face or limb detection: you choose the box (or, in the closet, the mask), and
the fit measures what is opaque inside it.

`FitError` (a `ValueError`) is raised, with the remedy in the message, when the silhouette
cannot be measured: an empty or tiny silhouette in the box (fewer than a handful of opaque rows
or pixels), a fully opaque box without a closet mask (the mascot has no transparent background to
measure), or inconsistent measurements. Move the body box over the body, use a transparent PNG,
or use a closet edit mask: with an explicit mask the mask is the silhouette, so an opaque mascot
works in the closet flow. The CLI reports `FitError` as an `error:` line with exit code 2.

The renderer exposes `cache_config = {"renderer": "local-composer", "templates": 3, "styles":
["classic", "streetwear"]}`. It is stored in the manifest, so a catalog made by an older template
revision (`templates: 2`) is rejected with a message to regenerate into a new output directory.
Change `templates` whenever the pixels of any outfit change.

The palette comes from your choice, or from the country profile when a library caller passes none.
Garment names in a profile are descriptions for a possible future provider; the default compositor
uses a fixed per-category template (padded coat, jacket, cardigan, t-shirt, sleeveless tee,
camisole) and does not interpret the text.

Existing transparency is preserved. An opaque background is not removed, and a fully opaque body
box fails with `FitError` unless a closet edit mask is used. See the limitations in the README.

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
