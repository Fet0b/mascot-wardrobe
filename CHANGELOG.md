# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses semantic versioning.

## [Unreleased]

### Added

- Review-snapshot coverage ledger, portable legacy four-band weather validation, timezone-aware
  overlay windows shared by portraits and PetDex, and synthetic safety tests. WhatsApp apply now
  backs up the observed original photo, locks staging/delivery, polls readback three times and
  atomically preserves last-good state. Combined apply preflights both profiles and restores
  Hermes on later WhatsApp failure where supported, reporting partial/rollback outcomes.
  Runtime/service boundaries and old avatar post-hook replacement are documented; no live
  runtime or schedule is changed.

- `collections list/select/remove`: stable selectors across saved closets, a separate atomic
  weather-publisher choice with repeated `--gateway hermes --gateway whatsapp`, and guarded
  removal of non-active named entries with `--yes` while retaining all item assets and inventory.
  Listing and removal accept legacy template-set-2/3/4 schema-1 manifests without migration.
  `publish-closet` (alias `weather-run`) can use the saved choice; `hermes` selects the active local
  Hermes PetDex pet/avatar using a Pillow atlas, digest-qualified slug, byte-equivalent reuse and
  CLI readback/rollback. WhatsApp uses the existing profile adapter. Both require `--apply` for
  writes; dry-run remains deterministic with no network calls or filesystem writes. No scheduler
  is installed, wired or activated. Read-only publishing/selection supports legacy collections
  without migration. Optional overlays accept inclusive dates in an explicit IANA timezone. A parity map and portable
  `scripts/check_local_parity.py --source ROOT --copy ROOT` support later code-only mirror review.
- `publish-closet`: selects exactly one item of a saved closet wardrobe by the country profile's
  weather category (missing, duplicate, stale or unsafe input fails closed) and renders its exact
  saved PNG as a 1024x1024 JPEG on `#102b32`, circular-safe, with an optional externally supplied
  overlay clipped to that area. The temperature comes from `--temperature` or from the weather
  updater's state JSON (`--state-file`, `updated_at_utc`). Dry-run is the default and writes
  nothing; `--apply` stages the JPEG and uses the existing WhatsApp adapter with readback and
  idempotence. The closet, inventory, active wardrobe and Telegram are never touched. An
  uninstalled scheduler example lives in `examples/closet-weather/`.
- Persistent format-agnostic inventory with stable garment numbers, transactional storage,
  backups, locking, hash verification and static PNG asset imports. Manifest and closet imports
  are idempotent; ambiguous closet identities are refused and explicit edits use hash guards.
- `inventory` commands for complete numbered listings, range boundaries, user-selected defaults,
  temperature selection, removal and verification. Interactive `change` / `mudar-peca` asks for
  a named range and then the user's garment number; suitability never filters selection and a
  missing default exits 3 without mutation. Inventory commands never contact channels.
- Automatic CLI registration for generated and closet garments (`generate --no-inventory` opts
  out), named suitability ranges in `closet piece`, and a generic Hermes skill template in the
  source distribution. Test and e2e inventory data is redirected to temporary homes.
- `mascot-wardrobe closet piece --country XX [--wardrobe NAME]`: an interactive loop that creates
  one piece at a time. It asks the intended temperature in Celsius (country-profile thresholds),
  style and palette separately for every piece, renders and saves only that item with provenance,
  allows a targeted revision that leaves other items untouched, then asks whether to make another.
  It never touches a channel; publishing stays a separate, explicitly confirmed step.
- Style and palette choices: `generate` and `closet add`/`edit` take `--style`
  (`classic`, `streetwear` or `auto`) and `--palette` (one to six `#RRGGBB` colours, or `auto`),
  with four palette roles (base, secondary, accent, highlight). `auto` is deterministic and local.
  On a terminal missing values are asked with prompts; the global `--no-input` flag forces the
  non-interactive rules. Choices and their provenance (`user`, `auto`, `default`, `profile`,
  `migrated`) are recorded in `manifest.json` and `closet.json`.
- `streetwear` style, built from fitted geometric layers. No external reference images are sent
  or fetched.
- `mascot-wardrobe closet migrate [--dry-run]`: upgrades a template-set-2 closet to the current
  renderer atomically, with a backup in `backups/closet-templates-2-<n>.json`, new items in
  `items-t3/` and the old `items/` and `catalog/` left untouched.
- `mascot-wardrobe closet` (`init`, `add`, `edit`, `remove`, `save`, `use`, `catalog`, `list`,
  `verify`): an identity-locked closet. One user mascot and an explicit binary edit mask are
  copied verbatim and pinned by sha256; items are rendered by the existing `LocalComposer` with
  protected pixels verified byte-identical; named wardrobes pin item versions and hashes and go
  stale instead of silently changing; the catalog contact sheet is built from the saved item
  files. Backward compatible: `generate`/`select`/`publish` and `manifest.json` are unchanged.
  Items carry an editable `--name` (default derived from the id) and `--description`
  (60/500 character limits, rejected rather than truncated), shown below each catalog tile;
  label-only edits keep the image, hash and version. Schema 1 closets without labels still open.
  See `docs/closet.md`.
- `mascot-wardrobe publish`: selects one outfit and delivers the same square JPEG to opt-in live
  adapters (Hermes WhatsApp bridge on loopback; Telegram bot profile via `setMyProfilePhoto`),
  exposing the atlas path. Dry-run by default, `--apply` required. Bounded, redirect-denying,
  redacted clients; Telegram reported as acknowledged, never verified. Uninstalled scheduler
  examples in `examples/publish/`. ADR 0003 amended; the sandbox channel is unchanged.
- `docs/integration.md`: clean install, verification, dry-run versus `--apply`, channel
  prerequisites, rollback, and the boundary with a separately installed Hermes scheduler.

### Changed

- Streetwear templates now have their own colour-block layouts (contrast sleeves, side panels,
  yokes, two-tone splits) that use the palette roles in large areas, instead of one shared layer
  stack. Fit, lines, protected pixels and clipping are unchanged. This stays within unreleased
  template set 3, so no new migration is needed.
- Template revision 3: garments are fitted to the silhouette measured from the image (neck,
  shoulders, torso, hem) instead of fixed fractions of the box, and are built from clipped layers.
  Catalogs and closets from template revision 2 are rejected: regenerate into a new directory, or
  run `closet migrate` for a closet.
- A fully opaque body box now fails with a clear `FitError` (exit 2) unless a closet edit mask is
  used; an empty or tiny silhouette fails the same way.
- `closet add --palette` is optional (library default or prompt); `closet edit` keeps stored
  style and palette when they are omitted.
- Non-interactive `generate` and `closet add` require explicit `--style` and `--palette`
  (exit 2 otherwise); pass `--style classic --palette auto` in scripts.

### Fixed

- Clothing alignment: the templates were plain rectangles over a body box narrower and shorter than
  the example mascot, leaving bare margins and hem, a bib-like warm and very hot top, and a
  floating mild sweater. `LocalComposer` now draws fitted rounded templates (torso, neckline,
  sleeves, hem, collar, zipper, buttons and cuffs) relative to the explicit body box and keeps the
  head untouched. The example mascot gets its own explicit body box
  (`0.125 0.555 0.875 0.915`, used by the README quickstart, `scripts/e2e-smoke.sh` and the demo
  images); the global default box is unchanged. The templates fit the box you give; there is still
  no anatomy detection.
- `LocalComposer.cache_config` records a template revision, so catalogs generated by the previous
  renderer (which recorded none) are rejected rather than reused. Regenerate into a new directory.
- `art/generate_art.py --check` now compares the outfit thumbnails of `hero.png` and
  `contact-sheet.png` (tolerating text rasterization), not only their sizes. All demo images were
  regenerated through the real pipeline.

### Changed

- The source distribution is now complete (explicit `MANIFEST.in`): docs, art, assets, examples,
  scripts, plans, tests, CI workflow and secret baseline are included so the README links resolve;
  the wheel stays runtime-only. A test builds the real sdist and inventories it.
- Hero banner re-laid out: the title and tagline fit the left column with a clear gap to the
  mascot, checked by a layout test.
- `scripts/e2e-smoke.sh` picks `$PYTHON`, else `.venv/bin/python`, else `python3`, requires the
  build module, and works in a fresh `run.*` directory without deleting existing evidence.
- `scripts/scan-secrets.sh` reports only status, counts and `type at file:line`, never raw hook
  output; `SCAN_ROOT` selects the scanned repository.
- README states the network scope precisely and qualifies hash reproducibility to the same Pillow
  toolchain; `docs/architecture.md` describes the actual dependency graph.
- `detect-secrets` is pinned to 1.5.0 in the dev extra and in CI.

## [0.1.0] - 2026-10-18

### Added

- Generate six local Pillow outfits once from a user PNG and a normalized body box; select by
  temperature from the manifest only.
- Country presets BR, GB, US and JP, custom profiles files, Celsius and Fahrenheit input.
- Optional Open-Meteo current temperature with explicit coordinates, GMT request, UTC
  comparison, 3 hour stale and 15 minute future limits, and bounded failure handling.
- Export-only channels (`whatsapp`, `telegram`, `discord`, `hermes-cli`) with idempotent file
  copies, and a loopback HTTP sandbox bridge for contract tests.
- Original mascot artwork, hero banner and contact sheet with a reproducible generator.
- Documentation, ADRs, traceability, CI workflow, secret scanning and an end-to-end smoke script.

### Changed

- Renamed the distribution from the prototype name to `mascot-wardrobe`; user-facing text is
  English; the sandbox header is `X-Mascot-Wardrobe-Sandbox`.
- Output defaults are relative to the working directory (`./mascot-wardrobe-out/`); no machine
  specific roots remain.

### Removed

- Private evidence files and machine-specific paths from the prototype.
