# ADR 0002: Generate once, select often, no inference

Status: accepted

## Context

Choosing an outfit by temperature must be cheap, repeatable and runnable from a scheduler.
Image generation with models is slow, costs money, needs credentials and is not reproducible.

## Decision

- Generation runs once per request and writes six outfits plus a manifest. It uses local
  deterministic Pillow overlays inside a configurable body box. There is no AI or LLM
  dependency, no semantic segmentation and no remote call.
- Selection reads only the manifest and returns paths of existing files. It never regenerates.
- Generation is idempotent: an identical request verifies and reuses existing assets, a
  different request against the same output is refused, and repeated generation yields
  identical hashes.
- The `Renderer` protocol is the extension point if someone later wants a different compositor.

## Consequences

- Output is deterministic and testable (hash comparisons).
- Quality is limited: overlays are rectangular and geometric, and opaque backgrounds are not
  removed. These limits are documented in the README.
- Garment text in profiles is only descriptive for the default compositor.
