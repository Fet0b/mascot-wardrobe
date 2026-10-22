# mascot-wardrobe

![mascot-wardrobe: generate once, select by weather](assets/hero.png)

Give it a PNG of your own mascot and it dresses the mascot for six temperature bands, then lets
the **weather choose the look**. The primary workflow is: keep a named saved wardrobe in an
identity-locked closet, read the current temperature from the state file your weather updater
already writes, and let `publish-closet` select the one matching saved look and plan its profile
photo. The outfits are rendered **once** with local Pillow overlays, so selecting is a lookup:
no image generation, no model, and no weather request or coordinates in that command.

```sh
# Dry-run (the default): prints the weather category, item id, slug, hash and a planned WhatsApp
# channel. Writes nothing, makes no request, never switches the active wardrobe.
# MASCOT_WARDROBE_WHATSAPP_BRIDGE_HEADER must be set in the environment (see below).
mascot-wardrobe publish-closet --closet-root closet --wardrobe my-looks --country BR \
  --state-file /path/to/weather/state.json \
  --whatsapp-catalog-dir /path/to/bridge/approved-photos \
  --whatsapp-state-dir /path/to/private/whatsapp-state \
  --whatsapp-bridge-url http://127.0.0.1:3000
```

The bridge header (`Name: value`, whatever your bridge requires) is read from the
`MASCOT_WARDROBE_WHATSAPP_BRIDGE_HEADER` environment variable, which you export from a private
environment file kept outside the repository. This documentation never contains a real header or
token. The three WhatsApp settings go together; without them the dry-run plans only the
selection and shows no WhatsApp channel.

`--apply` is the separate, explicit live-change flag: adding it to the same command stages the
JPEG and changes the WhatsApp profile photo through your bridge. Leave it off to stay in
dry-run. Nothing here installs a scheduler: hooking the command into an existing hourly job is
your explicit, opt-in step. See [docs/weather-workflow.md](docs/weather-workflow.md).

Manage saved collections across the default `~/.local/share/mascot-wardrobe` data home:

```sh
mascot-wardrobe collections list
mascot-wardrobe collections list --json
# Copy a stable selector ID from list; repeat --gateway to select both destinations.
mascot-wardrobe collections select SELECTOR_ID --gateway hermes --gateway whatsapp
mascot-wardrobe publish-closet --country BR --state-file /path/to/weather/state.json
# Remove only a non-active named entry; all item assets and inventory are retained.
mascot-wardrobe collections remove SELECTOR_ID --yes
```

Listing includes legacy template-set-2/3/4 closets without migration or writes. Removal also
works on legacy manifests, retaining their schema and all remaining data.
`collections select` saves a separate publisher choice and never switches the active wardrobe.
On a terminal, omit the ID and gateways to choose them interactively; scripts must supply the
ID and explicit `--gateway` flags. `hermes` means the **active Hermes PetDex pet/avatar**. `whatsapp` means the existing WhatsApp profile-photo adapter.
Both destinations require `--apply` for writes; the saved-selection dry-run above needs no bridge
settings and reports the planned gateways. Applying WhatsApp requires all three WhatsApp settings
shown above. See [saved publisher selection](docs/weather-workflow.md#saved-collections-and-publisher-selection)
for data-directory overrides and full multi-gateway commands. Hermes-only apply needs no WhatsApp
settings; it requires the Hermes Agent Python environment and CLI. See the
[local runtime parity map](docs/local-runtime-parity.md) before making a local code mirror or live change.

The original manifest workflow (`generate` → `select` → `demo`/`publish`) remains fully available
and backward compatible: its arguments, outputs and exit codes are unchanged, as described below.
A persistent numbered inventory also keeps garments beyond catalog folders and records your chosen
defaults.

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
  --body-box 0.125 0.555 0.875 0.915 --style classic --palette auto

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
where your mascot's body is: the templates are fitted to the silhouette measured inside that box
(neckline, shoulders, sleeves, hem); there is no face or limb detection. `--style` (`classic` or
`streetwear`) and `--palette` (one to six `#RRGGBB` colours, or `auto`) are asked on a terminal and
required otherwise (exit 2, nothing written), so scripts pass them explicitly. `auto` is
deterministic and local; no reference images are sent or fetched. See
[docs/wardrobe.md](docs/wardrobe.md).

## Persistent inventory and your defaults

`generate` and the closet item commands register saved garments automatically outside the
repository. Use `generate --no-inventory` to opt out, or the global `--data-dir PATH` before the
command to choose a data home. The inventory has stable numbers, independent per-item style,
palette and suitability ranges. Static PNG is the implemented asset adapter.

```sh
mascot-wardrobe inventory ranges --country BR
mascot-wardrobe inventory change --country BR
mascot-wardrobe inventory list --range cold --json
mascot-wardrobe inventory select --country BR --temperature 12
```

`change` (alias `mudar-peca`) asks for a named range, shows every garment regardless of
suitability, then saves the number you choose. Selection follows that mapping only; a missing
mapping returns `no-default` and exit 3 without changing active selection. It changes no live
photo or gateway. See [inventory and imports](docs/inventory.md) for scripts, data location and
guarded recovery.

## Identity-locked closet

`mascot-wardrobe closet` keeps one mascot as an immutable, hash-pinned source and an explicit
binary edit mask, lets you add, edit and remove clothing items, save and switch named wardrobes,
and builds a catalog sheet from the saved item files. Pixels outside the mask are verified
byte-identical to the source, and a changed mascot, mask or item is detected. A closet made by an
older release is upgraded with `closet migrate [--dry-run]` (backup, atomic). See
[docs/closet.md](docs/closet.md).

```bash
mascot-wardrobe closet init --mascot mascot.png --mask mask.png
mascot-wardrobe closet add coat --template very_cold --style classic --palette "#2878b5" "#e8b84b"
mascot-wardrobe closet save winter --items coat
mascot-wardrobe closet use winter && mascot-wardrobe closet catalog
```

## Publishing a profile photo (opt-in)

`mascot-wardrobe publish` selects one outfit and delivers the same square JPEG to the channels
you configure, and prints the matching atlas path for the Hermes pet. It is a **dry-run by
default**; nothing changes until you add `--apply` and a configured channel.

```sh
mascot-wardrobe publish --manifest mascot-wardrobe-out/catalog/manifest.json --country BR \
  --temperature 22 --whatsapp-catalog-dir /path/to/bridge/approved-photos \
  --whatsapp-state-dir /path/to/private/whatsapp-state \
  --whatsapp-bridge-url http://127.0.0.1:3000 --whatsapp-bridge-header "Name: value" \
  --telegram --telegram-expected-bot-id 123456789          # dry-run
```

Supported scope only: WhatsApp through the local Hermes/Baileys bridge (run it on the Hermes
host; loopback only) and the Telegram **bot itself** (not a personal account). The Telegram
token comes from the `MASCOT_WARDROBE_TELEGRAM_BOT_TOKEN` environment variable; Telegram results
are acknowledged but unverified. It needs no LLM or image API. See [docs/channels.md](docs/channels.md)
the uninstalled [scheduler examples](docs/scheduler.md) and the
[installation and integration guide](docs/integration.md), which also states that a separately
installed Hermes weather scheduler is not wired to this project.

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

- [Installation and integration guide](docs/integration.md)
- [Architecture](docs/architecture.md) and the [ADRs](docs/adr/)
- [Configuration, weather and time semantics](docs/configuration.md)
- [Wardrobe compositor and atlas layout](docs/wardrobe.md)
- [Weather-first workflow (primary use case)](docs/weather-workflow.md)
- [Identity-locked closet, wardrobes and catalog](docs/closet.md)
- [Persistent inventory, range defaults and imports](docs/inventory.md)
- [Channels (sandbox exports and opt-in live adapters)](docs/channels.md)
- [Scheduler examples (not installed)](docs/scheduler.md)
- [Traceability](docs/traceability.md) and [verification](docs/verification.md)

## Limitations

- No semantic understanding: the compositor is geometric. It does not detect faces or limbs
  and it does not draw realistic clothing.
- Overlays are geometric shapes fitted to the silhouette inside the body box you provide. A wrong
  box gives a wrong result.
- Opaque PNGs: existing transparency is preserved, but an opaque background is not removed. A body
  box that is fully opaque fails with a clear `FitError` (exit 2) unless a closet edit mask is
  used; use a transparent PNG or the closet.
- Country presets are product defaults, not meteorological or health standards, and one country
  can have many climates.
- Sandbox channels are export-only: a file and loopback test fixture, not a real account.
  Real profile changes happen only through `publish --apply`, limited to the Hermes WhatsApp
  bridge and a Telegram bot; Telegram is never verified, only acknowledged.
- Atlas animation is a geometric translation of the portrait, not an articulated animation.

## Security

- Sandbox use needs no credentials. `publish --apply` with Telegram reads a bot token from the
  environment only and never stores or prints it. There are no runtime model calls.
- Output paths reject `..` and symbolic links; file names and slugs are validated and input
  PNG size is bounded.
- The optional HTTP sandbox bridge binds to loopback only and is a local contract test.
- The only external request by default is the opt-in `--live-weather` call to Open-Meteo.
  Separately, the sandbox bridge and its client talk over loopback HTTP on your own machine.
  `publish --apply` additionally talks to your loopback Hermes bridge and, if enabled, to
  `api.telegram.org`.
- Nothing is installed as a service; scheduler files are `.example` templates only.

See [SECURITY.md](SECURITY.md) for reporting and scope, and [CONTRIBUTING.md](CONTRIBUTING.md)
to help out.

## License

MIT, for the code and the bundled artwork. See [LICENSE](LICENSE).
