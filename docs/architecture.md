# Architecture

mascot-wardrobe prepares six outfits for a mascot once, then selects one by temperature. It is
a small library plus a CLI. It installs no services, changes no real accounts and makes no
network calls except the opt-in Open-Meteo weather request.

## Domain language

- **Mascot**: an id, a source PNG path and a normalized body box. No face detection.
- **Country profile**: five strictly increasing Celsius boundaries that define six categories,
  plus a palette and garment descriptions.
- **Category**: `very_cold`, `cold`, `mild`, `ambient`, `warm`, `very_hot`. `mild` is the
  intentional sixth category.
- **Wardrobe**: the six outfits, generated once and recorded in `manifest.json`.
- **Selection**: choosing an existing outfit by temperature. It reads the manifest only.
- **Channel**: a destination for a selected outfit. All channels are export-only (files, or a
  loopback test bridge); none is a real account.

## Layers

The layering is pragmatic, not a claim of full enterprise architecture.

| Layer | Package | Responsibility |
| --- | --- | --- |
| Domain | `mascot_wardrobe.domain` | Pure values and rules: `Category`, `CountryProfile`, `Mascot`, `validate_temperature`, `to_celsius`. No I/O, no Pillow, no network. |
| Application | `mascot_wardrobe.application` | Use cases: `prepare_wardrobe`, `select_outfit`, `export_outfit`, `sync_sandbox`, and the `safe_output` path guard. |
| Adapters | `mascot_wardrobe.wardrobe` | `WardrobeAgent` (generation, manifest, idempotent reuse, atlas and JPEG assets). |
| | `mascot_wardrobe.wardrobe.composer` | Pillow compositor: `LocalComposer`, `OutfitBrief`, the `Renderer` protocol, `CATEGORIES`, `_validate_brief`. |
| | `mascot_wardrobe.channels` | `FileExportChannel`, `SandboxBridge` (loopback HTTP server) and `WhatsAppBridgeChannel` (its client). |
| | `mascot_wardrobe.infrastructure` | `policies.load_profile` (JSON profiles) and `weather.fetch_weather` (Open-Meteo). |
| Interface | `mascot_wardrobe.interfaces.cli` | `generate`, `select`, `sandbox`, `demo` commands. |

Dependencies point inwards: the CLI calls the application layer, which uses the domain, the
wardrobe agent, channels and infrastructure. The domain imports nothing from the other layers.

### The composer split

`wardrobe/composer.py` holds the Pillow compositor and its types. `wardrobe/__init__.py` holds
the use case (`WardrobeAgent`), atlas and JPEG helpers, path safety (`_safe_root`,
`_safe_target`) and request validation, and re-exports the composer names so
`from mascot_wardrobe.wardrobe import LocalComposer, OutfitBrief, Renderer` keeps working. The
`Renderer` protocol lets a different compositor be injected without touching temperature rules.

## Data flow

1. `generate`: `prepare_wardrobe` validates the output path with `safe_output`, loads the
   profile, and `WardrobeAgent.generate` renders six outfits and writes assets plus
   `manifest.json`. A repeat with an identical request verifies and reuses existing assets.
2. `select`: `select_outfit` reads and validates the manifest (size, country, six distinct
   categories, safe slug and file names), converts the temperature to Celsius, classifies it
   with the profile and returns the paths of the existing outfit. No image is touched.
3. `sandbox` and `demo`: the selection is exported. `--channel whatsapp` (the default) routes
   through the loopback bridge via `sync_sandbox`; `telegram`, `discord` and `hermes-cli` use
   `export_outfit` and `FileExportChannel`. Each runs twice to show idempotency.

## Safety rules

- `safe_output` resolves an output directory and rejects `..` and symbolic links anywhere in its
  ancestry. There is no machine-specific allowlist; the caller chooses the root.
- Names and slugs are validated (`[a-z0-9][a-z0-9-]*`), input PNG size is bounded (16
  megapixels, static PNG only), and the manifest size is bounded.
- Writes are atomic per file. Identical repeated exports do not rewrite files.
- Weather failures raise `WeatherError`; no temperature is ever invented.

## Presets and limits

BR, GB, US and JP are adjustable product presets (see `configuration.md`), not universal truth.
The compositor is geometric and does not understand images (see `wardrobe.md`). Decisions are
recorded in `docs/adr/`.
