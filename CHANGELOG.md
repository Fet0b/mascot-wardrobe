# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses semantic versioning.

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
