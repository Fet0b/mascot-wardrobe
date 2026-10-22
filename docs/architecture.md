# Architecture

mascot-wardrobe prepares six outfits for a mascot once, then selects one by temperature. It is
a small library plus a CLI. It installs no services. By default it changes no real accounts and
makes no external requests except the opt-in Open-Meteo weather request; the sandbox bridge uses
loopback only. The opt-in `publish --apply` path can change a WhatsApp profile through a local
bridge or a Telegram bot profile (see ADR 0003).

## Domain language

- **Mascot**: an id, a source PNG path and a normalized body box. No face detection.
- **Country profile**: five strictly increasing Celsius boundaries that define six categories,
  plus a palette and garment descriptions.
- **Category**: `very_cold`, `cold`, `mild`, `ambient`, `warm`, `very_hot`. `mild` is the
  intentional sixth category.
- **Wardrobe**: the six outfits, generated once and recorded in `manifest.json`.
- **Inventory**: persistent garments with stable numbers, independent style, palette and
  descriptive suitability ranges; retired numbers are never reused.
- **Default**: a category-name-to-garment-number mapping explicitly chosen by the user.
- **Selection**: manifest selection retains its legacy lookup; inventory selection follows the
  exact saved category default and updates active state only when a mapping exists.
- **Channel**: a destination for a selected outfit. Sandbox channels are export-only (files, or a
  loopback test bridge). Live adapters are separate and opt-in.

## Structure and dependencies

This is one wardrobe domain context. Weather and channels are supporting adapters around it.
The layering is pragmatic and is not a claim of full enterprise architecture or of dependency
inversion.

| Package | Responsibility |
| --- | --- |
| `mascot_wardrobe.domain` | Pure values and rules: profiles, temperature classification and immutable inventory/garment/asset values and numbering/default policies. No I/O, no Pillow, no network. |
| `mascot_wardrobe.application` | Use cases: `prepare_wardrobe`, `select_outfit`, `export_outfit`, `sync_sandbox`, `publish_outfit` (dry-run unless applied) and the `safe_output` path guard. |
| `mascot_wardrobe.wardrobe` | `WardrobeAgent` (generation, manifest, idempotent reuse, atlas and JPEG assets). |
| `mascot_wardrobe.wardrobe.composer` | Pillow compositor: `LocalComposer`, `OutfitBrief`, the `Renderer` protocol, `CATEGORIES`, `_validate_brief`. |
| `mascot_wardrobe.wardrobe.styles` | Style registry (`classic`, `streetwear`), the four palette roles and the deterministic `auto` resolvers. Standard library only. |
| `mascot_wardrobe.wardrobe.fit` | `measure_fit` and `FitError`: measures the neck, shoulders, torso and hem from the silhouette (and the closet mask) so garments follow the image. |
| `mascot_wardrobe.interfaces.prompts` | Interactive style and palette prompts with injected input and output; used only by the CLI on a terminal. |
| `mascot_wardrobe.interfaces.piece` | `run_piece_session`: the interactive `closet piece` loop (one piece at a time, with injected input and output); calls the closet to render and save only that item. |
| `mascot_wardrobe.channels` | Supporting adapters: `FileExportChannel`, `SandboxBridge` (loopback HTTP server) and `WhatsAppBridgeChannel` (its client). |
| `mascot_wardrobe.channels.live` (`channels/live.py`) | Opt-in live adapters: `HermesWhatsAppProfileAdapter`, `TelegramBotProfileAdapter`, `telegram_transport`, `publish_to_catalog`. Never used by the sandbox commands. |
| `mascot_wardrobe.infrastructure` | Supporting adapters: profiles, weather, transactional `inventory_store` and `asset_formats` validation. |
| `mascot_wardrobe.application.inventory` | Import identities, guarded replacement, range defaults, complete inventory views and temperature selection. |
| `mascot_wardrobe.interfaces.cli` | Inventory, closet, generation, selection, sandbox, demo and opt-in publication entry points. |

The actual import graph: the CLI (interfaces) calls the application layer, and imports
`infrastructure.weather` itself for the live weather option. The application imports the concrete
`infrastructure.policies`, `wardrobe` and `channels` modules and the domain. `wardrobe` and
`infrastructure` also import the domain. The domain imports nothing from the other packages.
Legacy paths use concrete adapters. Inventory contracts explicitly expose transactions, asset
staging, reads and verification, while the `AssetFormat` protocol isolates image validation.

The `Renderer` protocol remains one real seam: `WardrobeAgent` accepts any object implementing it,
so a different compositor can be injected without touching temperature rules. Inventory adds
storage and asset-format seams; only the static PNG format adapter is implemented. Its module
owns Pillow-based inventory validation; the inventory domain never interprets formats.

### The composer split

`wardrobe/composer.py` holds the Pillow compositor and its types. `wardrobe/__init__.py` holds
the use case (`WardrobeAgent`), atlas and JPEG helpers, path safety (`_safe_root`,
`_safe_target`) and request validation, and re-exports the composer names so
`from mascot_wardrobe.wardrobe import LocalComposer, OutfitBrief, Renderer` keeps working. The
`Renderer` protocol lets a different compositor be injected without touching temperature rules.

## Data flow

1. `generate`: the CLI resolves `--style`/`--palette` (prompting only on a terminal),
   `prepare_wardrobe` validates the output path with `safe_output`, loads the
   profile, and `WardrobeAgent.generate` renders six outfits and writes assets plus
   `manifest.json`. A repeat with an identical request verifies and reuses existing assets.
   The CLI then registers the manifest in persistent inventory unless `--no-inventory` was
   supplied. The library generation path does not register it.
2. `select`: `select_outfit` reads and validates the manifest (size, country, six distinct
   categories, safe slug and file names), converts the temperature to Celsius, classifies it
   with the profile and returns the paths of the existing outfit. No image is touched.
3. `sandbox` and `demo`: the selection is exported. `--channel whatsapp` (the default) routes
   through the loopback bridge via `sync_sandbox`; `telegram`, `discord` and `hermes-cli` use
   `export_outfit` and `FileExportChannel`. Each runs twice to show idempotency.
4. `inventory change`: ask a named range, show every garment by number and save the user's
   chosen number. Suitability is a hint. `inventory select` classifies temperature and follows
   only this mapping; missing mappings return `no-default` without mutation or fallback.
5. Inventory imports validate assets before committing a collection. Closet identity is mascot
   hash, mask hash and item id; ambiguous duplicates fail, while explicit edits need a matching
   previous hash. See [inventory](inventory.md) and ADR 0005 for recovery contracts.

## Safety rules

- `safe_output` resolves an output directory and rejects `..` and symbolic links anywhere in its
  ancestry. There is no machine-specific allowlist; the caller chooses the root.
- Names and slugs are validated (`[a-z0-9][a-z0-9-]*`), input PNG size is bounded (16
  megapixels, static PNG only), and the manifest size is bounded.
- Writes are atomic per file. Identical repeated exports do not rewrite files.
- Inventory transactions serialize under an exclusive lock, validate every referenced hash,
  back up changed JSON and recover interrupted asset replacements at the JSON commit point.
  Inventory data defaults outside the repository; tests and e2e use temporary homes.
- Weather failures raise `WeatherError`; no temperature is ever invented.

## Presets and limits

BR, GB, US and JP are adjustable product presets (see `configuration.md`), not universal truth.
The compositor is geometric and does not understand images (see `wardrobe.md`). Decisions are
recorded in `docs/adr/`.
