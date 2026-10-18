# mascot-wardrobe

![mascot-wardrobe: generate once, select by weather](assets/hero.png)

Give it a PNG of your own mascot and it dresses the mascot for six temperature bands. The six
outfits are generated **once** with local Pillow overlays. After that, picking the right outfit
for a temperature is a lookup in a manifest: no image work, no network, no model.

## Why generate once, select often

- Generation is the only step that touches pixels. It is local, deterministic and idempotent.
- Selection reads the manifest and returns the path of an outfit that already exists, so it is
  instant, repeatable and safe to run from a scheduler every few hours.
- There is no AI or LLM dependency and no recurring inference cost. The same input always gives
  the same output hashes when run with the same Pillow and libjpeg toolchain; a different Pillow
  wheel can encode pixels or JPEG bytes differently.

![Six generated outfits](assets/contact-sheet.png)

The image above is `assets/contact-sheet.png`: six outfits made by the real pipeline from the
bundled example mascot with the BR preset. Both images and the mascot itself are original
artwork, reproducible with `art/generate_art.py` (see `art/README.md`).

## Install

Requires Python 3.11 or newer. From a checkout of this repository, in a clean virtual
environment:

```sh
python -m venv .venv && . .venv/bin/activate && pip install .
mascot-wardrobe --help
```

For development use `pip install -e '.[dev]'`.

## Quickstart

Run these from the root of the repository checkout, where `examples/mascot.png` lives. Outputs
go to `./mascot-wardrobe-out/` by default (ignored by git). Replace the mascot path and the
body box with your own to use your own mascot.

```sh
# 1. Generate six outfits once (country is always explicit; no geolocation).
# The example mascot ships with its own explicit body box (arms to hem, below the head).
mascot-wardrobe generate --mascot examples/mascot.png --id sunny --country BR \
  --body-box 0.125 0.555 0.875 0.915

# 2. Select by temperature as often as you like (28 C is "warm" in the BR preset).
mascot-wardrobe select --manifest mascot-wardrobe-out/catalog/manifest.json \
  --country BR --temperature 28

# 3. Export the selection to a channel folder; a Fahrenheit input works too.
mascot-wardrobe demo --manifest mascot-wardrobe-out/catalog/manifest.json \
  --country BR --temperature 77 --unit F --channel telegram
```

`demo` performs the export twice and shows `"action": "exported"` followed by
`"action": "unchanged"` with `"remote_sync": false`: the second run does not rewrite anything.
Files are copied to `mascot-wardrobe-out/exports/<channel>/<slug>/`.

Use `--body-box X1 Y1 X2 Y2` (normalized, default `0.22 0.60 0.78 0.88`) to tell the compositor
where your mascot's body is: the templates are fitted to that box (sleeves, hem, neckline), they
do not detect anatomy. See [docs/wardrobe.md](docs/wardrobe.md).

## Offline deterministic demo

Everything above is offline. `scripts/e2e-smoke.sh` runs the whole flow against a freshly built
wheel in a throwaway virtual environment: it generates twice and compares hashes and mtimes,
selects one outfit per category plus a Fahrenheit input, and runs `demo` twice to prove the
second run is `unchanged`. It never passes `--live-weather`. It uses `$PYTHON`, else
`.venv/bin/python`, else `python3`, and that interpreter needs the build module
(`pip install build`); the outputs go to a fresh run directory and nothing existing is deleted.

## Live weather (optional)

Weather is opt-in and never guessed. You pass explicit coordinates; the tool never geolocates:

```sh
mascot-wardrobe select --manifest mascot-wardrobe-out/catalog/manifest.json \
  --country BR --live-weather --latitude -23.55 --longitude -46.63
```

It calls the public Open-Meteo forecast API for the current temperature in Celsius. If the
service fails, times out, answers with bad data or the reading is older than 3 hours or more
than 15 minutes in the future, the command exits with status 2 and an `error:` line. It never
substitutes an invented temperature. Details are in [docs/configuration.md](docs/configuration.md).
The automated tests use a fake transport; a live call is an optional manual smoke check.

## Country presets and configuration

BR, GB, US and JP presets define five strictly increasing Celsius boundaries (six categories).
A boundary value belongs to the upper category. The presets are adjustable product defaults,
not universal truth, and you can supply your own profiles file. All boundary tables (Celsius and
Fahrenheit) and the profile schema are in [docs/configuration.md](docs/configuration.md).

## Documentation

- [Architecture](docs/architecture.md) and the [ADRs](docs/adr/)
- [Configuration, weather and time semantics](docs/configuration.md)
- [Wardrobe compositor and atlas layout](docs/wardrobe.md)
- [Channels (sandbox exports, not real accounts)](docs/channels.md)
- [Scheduler examples (not installed)](docs/scheduler.md)
- [Traceability](docs/traceability.md) and [verification](docs/verification.md)

## Limitations

- No semantic understanding: the compositor is geometric. It does not detect faces or limbs
  and it does not draw realistic clothing.
- Overlays are rectangular shapes clipped to the body box you provide. A wrong box gives a
  wrong result.
- Opaque PNGs: existing transparency is preserved, but an opaque background is not removed.
  Overlays are painted inside the body box only.
- Country presets are product defaults, not meteorological or health standards, and one country
  can have many climates.
- Channels are export-only. The sandbox is a file and loopback test fixture, not a real account.
  This project does not change any real account, and Telegram bot avatar changes are not
  available in this project.
- Atlas animation is a geometric translation of the portrait, not an articulated animation.

## Security

- No credentials or tokens are needed or read. There are no runtime model calls.
- Output paths reject `..` and symbolic links; file names and slugs are validated and input
  PNG size is bounded.
- The optional HTTP sandbox bridge binds to loopback only and is a local contract test.
- The only external request is the opt-in `--live-weather` call to Open-Meteo. Separately, the
  sandbox bridge and its client talk to each other over loopback HTTP on your own machine.
- Nothing is installed as a service; scheduler files are `.example` templates only.

See [SECURITY.md](SECURITY.md) for reporting and scope, and [CONTRIBUTING.md](CONTRIBUTING.md)
to help out.

## License

MIT, for the code and the bundled artwork. See [LICENSE](LICENSE).
