# ADR 0005: Persistent format-agnostic garment inventory

Status: accepted.

## Context

Generated catalogs and identity-locked closets preserve rendered files but do not provide a
persistent numbered garment collection or record the user's preferred garment for each range.
Those rules should apply to any mascot and must not depend on a renderer or file format.

## Decision

Keep inventory values and policies in the pure domain. Application use cases manage import,
identity, defaults and selection through explicit storage and asset-format contracts.
Infrastructure provides transactional JSON storage with exclusive locking, hash validation,
backups and crash recovery, and the first static PNG adapter. Only that adapter interprets
image bytes; no GIF, SVG or Lottie support is claimed.

Assign monotonic visible numbers and retain retired numbers. Store style, palette and ranges
independently; suitability never restricts selection. A default is exactly the user's chosen
range-to-number mapping. Temperature classification uses the existing country profiles;
missing defaults return `no-default` without mutation or fallback.

Closet identity is mascot hash, mask hash and item id, without a UUID or schema change.
Identical assets are idempotent; ambiguous different bytes fail atomically. Explicit replacement
requires the previous hash. Narrow migration and failed-registration recovery use that same
guard. CLI output registration follows successful catalog or closet saves; library APIs retain
their existing behavior.

## Consequences

Inventory data persists outside the repository by default and every test and e2e run redirects
it to temporary data. The PNG/mask compositor retains its fit and identity guarantees. A new
asset format requires an explicit adapter, rather than changes to selection rules. Inventory
commands neither publish nor install schedulers; external integrations need separate approval.
