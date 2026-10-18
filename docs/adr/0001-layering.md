# ADR 0001: Pragmatic layering

Status: accepted

## Context

The project began as a prototype whose wardrobe module mixed the use case, the Pillow compositor
and path safety. The goal is a small, testable library with a replaceable renderer and no
dependency from the rules on I/O.

## Decision

Keep five packages with inward dependencies:

- `domain`: pure values and rules (`Category`, `CountryProfile`, `Mascot`, temperature
  validation and conversion).
- `application`: use cases `prepare_wardrobe`, `select_outfit`, `export_outfit`,
  `sync_sandbox` and the `safe_output` guard.
- Adapters: `wardrobe` (generation, manifest, atlas) with the Pillow compositor split into
  `wardrobe/composer.py`; `channels` (file export and loopback bridge); `infrastructure`
  (profile loading and Open-Meteo).
- `interfaces.cli`: argument parsing and exit codes.

The composer (`LocalComposer`, `OutfitBrief`, `Renderer`, `CATEGORIES`, `_validate_brief`) moved
to `wardrobe/composer.py` and is re-exported from `wardrobe/__init__.py`, so existing imports
keep working. `_safe_root` and `_safe_target` stay in `wardrobe/__init__.py` because tests and
the use case import them there. `WardrobeAgent` accepts any object implementing `Renderer`.

## Consequences

- Temperature rules can be tested without Pillow, network or files.
- A different compositor can be injected without touching the rules.
- The split is limited to where it pays off. The wardrobe use case and its asset helpers still
  share one module, and this is not a claim of full domain-driven design.
