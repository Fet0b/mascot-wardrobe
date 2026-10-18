# Artwork

The mascot, banner and contact sheet are original work by the Mascot Wardrobe
contributors, released under the MIT license (see the repository `LICENSE`).

| File | What it is |
| --- | --- |
| `art/mascot.svg` | Hand-editable vector source (named groups, comments) |
| `examples/mascot.png` | 512x512 transparent PNG used as the example input |
| `assets/hero.png` | 1600x500 README banner |
| `assets/contact-sheet.png` | Six outfits made by the real `prepare_wardrobe` pipeline (BR preset) |

Everything is produced by `art/generate_art.py` from one shape list, so the SVG
and PNG always match. The generator needs only Pillow and uses no network.

```sh
.venv/bin/python art/generate_art.py          # regenerate into the repository
.venv/bin/python art/generate_art.py --out D  # same layout under directory D
.venv/bin/python art/generate_art.py --check  # exit 1 if committed art is stale
```

To restyle the mascot, edit the `GROUPS` list in the script. The demo images use the explicit
`EXAMPLE_BODY_BOX` (0.125, 0.555, 0.875, 0.915) from the script; the head and face stay clear of
every outfit (the compositor neckline follows the lower head), and the default body box
(0.22, 0.60, 0.78, 0.88) still starts below them. `hero.png` and `contact-sheet.png` contain text
rendered with the font bundled in Pillow, so `--check` compares only their outfit thumbnails (mean
pixel difference per thumbnail), which come from the pipeline and catch stale clothes, and ignores
text pixels.
