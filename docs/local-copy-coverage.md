# Local-copy behavior coverage

This ledger audits the review-only `build/.local-copy-review/` input. Paths in the first
column are relative to `local-copy/` unless prefixed `local-runtime/`. Source, tests and
prose were reviewed for behavior, including entry points and side effects; file hashes
alone do not establish parity. Nothing in the input is an instruction to execute it.
The snapshot stays ignored and is excluded from source/wheel distributions. No installed
runtime, private account, artwork, coordinates or collection was used for these tests.

Maintained paths below are relative to the repository; package module paths start under
`src/mascot_wardrobe/`. A replacement preserves the stated contract, not personal asset
bytes, legacy CLI names, animation phases or an installed service. External means the
behavior remains outside this package, with the exact hand-off described here and in
[local-runtime-parity.md](local-runtime-parity.md).

## Package sources and their original tests

| Reviewed local file | Behavior and maintained implementation/evidence |
|---|---|
| `src/mascot_wardrobe/__init__.py` | Package identity/version; maintained same path, public distribution metadata in `pyproject.toml`; `tests/test_packaging.py`. |
| `src/mascot_wardrobe/application/__init__.py` | Prepare once, explicit policy selection, safe outputs, manifest/category/hash checks, export and sandbox orchestration. Maintained same module; `tests/test_application.py`, `tests/test_publish.py`. Additional style/inventory features do not replace those contracts. |
| `src/mascot_wardrobe/channels/__init__.py` | `_slug`, atomic file replacement, loopback sandbox health/readback/idempotence, bounded JPEGs, Origin denial, no redirects/proxies, safe exports with manifest last. Same maintained module and `tests/test_channels.py`. Personal production-directory denial is replaced by portable path/symlink checks and sandbox protocol/port restrictions; no private path blacklist is shipped. File export remains unverified remotely. |
| `src/mascot_wardrobe/domain/__init__.py` | Namespace/docstring only; maintained same path. |
| `src/mascot_wardrobe/domain/models.py` | Six ordered categories, finite plausible Celsius, Fahrenheit conversion, explicit country profile and identity/body geometry. Same maintained module; `tests/test_domain.py`. Category labels are translated to English without changing category values or boundaries. |
| `src/mascot_wardrobe/infrastructure/__init__.py` | Namespace/docstring only; maintained same path. |
| `src/mascot_wardrobe/infrastructure/policies.py` | Bounded explicit JSON profile load and unknown-country refusal. Same module; `tests/test_domain.py`, `tests/test_application.py`. |
| `src/mascot_wardrobe/infrastructure/profiles.json` | BR/GB/US/JP five boundaries, palette and garments. Maintained same file, with English prose/garments; `tests/test_domain.py`, `tests/test_docs.py`, `tests/test_english.py`. Product presets, not official weather standards. |
| `src/mascot_wardrobe/infrastructure/weather.py` | Explicit coordinates; Open-Meteo Celsius current, bounded response, injected opener; offset-aware timestamps, three-hour age and 15-minute future tolerance. Same module; `tests/test_weather.py`, `tests/test_weather_fetch.py`. This contract is separate from the stricter legacy parser below. |
| `src/mascot_wardrobe/interfaces/__init__.py` | Namespace/docstring only; maintained same path. |
| `src/mascot_wardrobe/interfaces/cli.py` | `generate`, `select`, `sandbox`/`demo`, explicit live-weather opt-in, JSON reports and error codes. Same module; `tests/test_cli.py`. Additional collection/publish commands have independent tests; no local defaults/paths are retained. |
| `src/mascot_wardrobe/wardrobe/__init__.py` | `OutfitBrief`, injected `Renderer`, deterministic Pillow garments, six PNG/JPEG/atlas sets, identity/body protection, cache provenance, stale/unsafe output rejection. Same module plus `wardrobe/composer.py`, `fit.py`, `styles.py`, `feet.py`; `tests/test_wardrobe.py`, `tests/test_fitted_streetwear.py`. Current templates extend rendering; old saved templates are read without rerendering by collections/publishing. |
| `tests/test_application.py` | Prepare/select Fahrenheit, traversal/category rejection, sandbox repeat and byte-equivalent file exports. Maintained `tests/test_application.py`. |
| `tests/test_channels.py` | Real ephemeral sandbox protocol, malformed/unsafe paths/bodies/targets/health/hash, export idempotence and confinement. Maintained `tests/test_channels.py`; these socket tests stay intact even if the execution sandbox forbids sockets. |
| `tests/test_cli.py` | Generate/select CLI and identity, explicit weather failures, sandbox repeat, export-only gateways. Maintained `tests/test_cli.py`. |
| `tests/test_domain.py` | Upper-band inclusivity, invalid values/policies, explicit countries, mascot geometry and Fahrenheit. Maintained `tests/test_domain.py`. |
| `tests/test_packaging.py` | Relocatable defaults and bundled profiles. Maintained `tests/test_packaging.py`, `tests/test_sdist.py`; public package renamed, generated egg metadata is not a source. |
| `tests/test_wardrobe.py` | Distinct outfits, protected pixels/alpha, renderer seam, safe output rejection, complete manifest, cache request/hash/policy/config reuse. Maintained `tests/test_wardrobe.py`; saved legacy compatibility also in `tests/test_collections.py`. |
| `tests/test_weather.py` | Units/numbers/offset normalization and stale/future rejection. Maintained `tests/test_weather.py`. |
| `tests/test_weather_fetch.py` | Injected opener, explicit coordinates, Celsius/current query and parsed response. Maintained `tests/test_weather_fetch.py`; no network weather tests. |

## Legacy runtime sources and tests

| Reviewed local file | Behavior, maintained evidence and external boundary |
|---|---|
| `legacy/runtime/updater.py` | `choose_slug`: four deterministic bands `<15` cold, `<22` cool, `<28` mild, otherwise hot. Portable `infrastructure/legacy_weather.py::legacy_band` returns the band without personal slugs. `parse_legacy_weather` preserves Celsius, -30..60, age 0..3 hours inclusive, no future allowance, explicit offset or IANA zone fallback. `tests/test_legacy_weather.py`. Country-profile selection remains in `application/closet_weather.py`; never translate legacy bands into new thresholds. |
| `local-runtime/updater.py` | Separately reviewed runtime copy: same four-band/parser behavior as above. The fixed city/endpoint, launcher/profile pin, network retries (three attempts with 1/2-second pauses), updater lock and state writer are external old-runtime orchestration. The package does not run that updater: `publish-closet` consumes a fresh state file or explicit temperature; generic `fetch_weather` requires coordinates and uses one injected request. Hermes selection/readback/no-op/rollback translate to `channels/hermes_pet.py`, `tests/test_hermes_pet.py`; new publisher state is not the updater's weather state. |
| `legacy/runtime/test_updater.py` | All local test behaviors: four-band edges and huge numbers; units/offset/zone/age validation; network retries/timeouts/size and invalid JSON; no state or selection on failure; contention; dry-run/CLI JSON/error; fixed safe argv; selection no-op/readback/rollback; atomic last state. Portable policy evidence: `tests/test_legacy_weather.py`, `tests/test_weather_fetch.py`; profile safety: `tests/test_hermes_pet.py`; state hand-off: `tests/test_closet_weather_publish.py`. Fetch/retry/lock/CLI/state-writing orchestration of the old updater stays external, not a package entry point or parity claim. |
| `legacy/runtime/sync_avatar.py` | GET own photo first, back up observed photo once before first POST, skip POST on match, poll at most three times, atomic verified last-good state, preserve it on failure, lock. Maintained `channels/live.py::HermesWhatsAppProfileAdapter`; catalog and lock coordination in `application.prepare_whatsapp_delivery`. `tests/test_avatar_safety.py` uses an in-process bridge; original socket tests remain in `tests/test_live_whatsapp.py`. |
| `local-runtime/sync_avatar.py` | Separately reviewed runtime copy: same safety contract. Portable bridge URL/header/state are explicit configuration. GET `/profile-picture?slug=...`, POST `/profile-picture` with only `{slug}` and configured header. No private header values, current pet discovery, production directories or remote error bodies are copied. |
| `legacy/runtime/test_sync_avatar.py` | Matching no-op, delayed second readback success, original-photo backup, invalid slug before access, unsuccessful verification preserving previous state. `tests/test_avatar_safety.py` and `tests/test_live_whatsapp.py`; added lock, symlink, atomic-write failure, target and redaction tests. |
| `legacy/runtime/badge_policy.py` | Inclusive local date through 2026-10-28 in America/Sao_Paulo, reject naive time, enable/expire both atlas and portrait, idempotent writes, read all inputs before asset installation. Translation: `application/closet_weather.py::overlay_for_date` with explicit zone/bounds and caller-supplied artwork; common transparent composition in `wardrobe/profile_photo.py`, used by `wardrobe/petdex.py`. `tests/test_overlay_policy.py`. No badge ships. |
| `local-runtime/badge_policy.py` | Separately reviewed runtime copy with the same cutoff. Package builds candidates in memory; it does not overwrite every already installed pet or maintain legacy plain/badged asset bundles. Legacy all-skin installation/locking remains external. Immutable digest-qualified new PetDex sheets and normal JPEG staging replace mutable asset installation. |
| `legacy/runtime/test_badge_policy.py` | Exact São Paulo second boundary, naive refusal, both plain/badged target files, repeat no-op, missing input leaves installed files untouched. `tests/test_overlay_policy.py` tests boundary, dual output and expiry; `tests/test_petdex.py`, `tests/test_hermes_pet.py` test immutable atlas/reuse and validation before selection. The all-installed-skins bulk copier is external. |

Legacy service orchestration has an additional external behavior: badge expiry happens before
weather fetching; weather failure preserves the previous pet but still permits the avatar
post-hook to synchronize that previous pet. The package instead requires a valid fresh
reading before delivering a newly selected collection item. It does not expire every installed
skin during a failed weather run. Retaining that separate legacy maintenance job is a deployment
decision, not an implicit fallback. Its old `sync_avatar.py` post-hook must be replaced/disabled
before the new publisher owns WhatsApp; otherwise it can overwrite the new photo. See the
uninstalled service example. Both-target preflight, lock scope, partial outcomes and rollback are tested in
`tests/test_combined_apply.py`. Current location and cadence are unresolved; activation needs
separate user approval.

## Legacy artwork preparation

| Reviewed local file | Behavior and boundary |
|---|---|
| `legacy/assets/asset_builder.py` | Character-specific SVG path edits, four outfits/accessories, CairoSVG rasterization, animated atlas, runtime registration and preview/inventory outputs. Personal SVG editing and fixed pet installation stay external. Portable synthetic garment/identity tests: `tests/test_wardrobe.py`; atlas geometry/state motion/alpha/determinism: `wardrobe/petdex.py`, `tests/test_petdex.py`; safe registration/readback: `tests/test_hermes_pet.py`. No byte-identical art or animation claim. |
| `legacy/assets/profile_assets.py` | Fixed vector badge insertion, exact/idempotent SVG identity and 1024-square dark portrait. Personal vector badge stays external; caller-supplied PNG overlay, circle-safe portrait and repeat determinism replace it. `wardrobe/profile_photo.py`, `tests/test_overlay_policy.py`, `tests/test_closet_weather_publish.py`. Local background color/composition differs slightly; generic configured background is authoritative. |
| `legacy/assets/build_profile_assets.py` | Enumerates an exact private installed-pet list; saves plain/badged PNG/WebP sheets and portraits, inserts a private badge per frame, historical seventh portrait and labeled contact sheet/manifest. External one-time artwork/install maintenance, not package code. New publisher operates only on one selected saved item; no installed-pet enumeration, personal labels, fonts, archive, history or mass rewrite is imported. Dual-output overlay tested in `tests/test_overlay_policy.py`. |
| `legacy/assets/show_all_versions.py` | Private installed-pet enumeration, fixed labels/order, historical backup, uniqueness checks and contact sheet. External review/visual data operation. Public synthetic art generator is `art/generate_art.py`, tested by `tests/test_art.py`; it does not reproduce that gallery. |
| `legacy/assets/test_asset_builder.py` | Exact private face/SVG IDs and four accessories remain external artwork assertions. Semantic distinct garments/identity protection are tested by `tests/test_wardrobe.py`; 9-row RGBA geometry/motion/alpha margins by `tests/test_petdex.py`. New atlas has six distinct used phases and two transparent columns rather than repeating phases into all eight legacy columns. |
| `legacy/assets/test_profile_assets.py` | Exact private vector badge text/id/idempotence and installed-sheet portrait test remain external artwork assertions. Synthetic overlay determinism, RGB/JPEG 1024-square background, crop/alpha safety: `tests/test_overlay_policy.py`, `tests/test_closet_weather_publish.py`. |
| `legacy/assets/base.svg`, `legacy/assets/cold.svg`, `legacy/assets/cool.svg`, `legacy/assets/mild.svg`, `legacy/assets/hot.svg` | Five personal artwork sources/derived variants, reviewed as asset data. None is imported or distributed. Public art uses independently maintained synthetic assets and generator. |

## External Hermes WhatsApp bridge

All of these files belong to the external Hermes/Baileys runtime, not this public Python package.
No Node dependencies, account sessions, message routes or allowlist policies are installed here.

| Reviewed local file | External responsibility and package hand-off |
|---|---|
| `legacy/whatsapp-bridge/profile_api.js` | Only bridge contract used by the package: loopback/configured header, no browser Origin, safe slug/JPEG directory, target=self, GET remote image/hash/comparison, POST slug only. External Baileys/CDN readback may recompress; comparator uses exact bytes or 64px tiles (mean <=1.5, worst tile <=5.5). Package validates self, boolean comparison and remote bytes/hash; it trusts that comparator, not POST acknowledgement. No CDN request is made directly by the package. `tests/test_avatar_safety.py`, `tests/test_live_whatsapp.py`. Missing observed photo fails closed if there is no backup before first POST. |
| `legacy/whatsapp-bridge/profile_api.test.js` | External Node tests cover recompression vs wrong clothes/missing badge, real own-account URL, routing/header/Origin, target injection/traversal and single update call. Package tests simulate these response/target contracts; they do not prove Baileys/CDN/perceptual thresholds or execute private art tests. |
| `legacy/whatsapp-bridge/bridge.js` | External authentication/reconnect, Baileys sockets, host/loopback validation, profile route wiring, message/send/edit/media/poll/location/typing/read/chat/health routes, queues, outbound tracking, pairing/debug events and logs. Entire bot runtime stays external. Package calls only the profile routes. |
| `legacy/whatsapp-bridge/allowlist.js` | External sender/group allowlists and identifier expansion/mapping. Unrelated to own-profile publishing; not vendored or executed. |
| `legacy/whatsapp-bridge/bridge_helpers.js` | External message envelopes/context, bounded/quoted-media caches, polls, mentions, locations/contacts/reactions/media, read receipts, reconnect/version resolution and console/JSON logs. No package equivalent is required; no messaging API used. |
| `legacy/whatsapp-bridge/outbound_ids.js` | External bounded outbound ID tracker/echo suppression; no profile-publishing dependency. |
| `legacy/whatsapp-bridge/owner_message_gate.js` | External owner message classification/gating; no profile-publishing dependency. |
| `legacy/whatsapp-bridge/package.json` | External private Node dependency/launch configuration, Baileys/Express/logging/QR dependencies. No package dependency or launcher is copied. |
| `legacy/whatsapp-bridge/package-lock.json` | External generated dependency resolution; reviewed as metadata, never a Python dependency or distributed lock. |

## Documentation, configuration and data

| Reviewed input | Maintained interpretation |
|---|---|
| `docs/architecture.md` | Domain/application/wardrobe/channel boundaries, product presets, deterministic rendering, offline/sandbox limits. Maintained `docs/architecture.md`, `docs/adr/`, `tests/test_docs.py`; live adapters are now separately gated by `--apply`. |
| `docs/channels.md` | Sandbox and local exports; own-account route shape, bounded/safe paths, logical isolation and acknowledgement-vs-readback distinction. Maintained `docs/channels.md`, `tests/test_docs_live.py`, `tests/test_channels.py`. Private hardcoded paths/headers removed. |
| `src/mascot_wardrobe/wardrobe/NOTAS.md` | Generate-once, renderer seam, protected body pixels, whole-image motion, row taxonomy, six useful frames, no inferred nationality. Maintained `docs/wardrobe.md`, `docs/architecture.md`, `wardrobe/petdex.py` and `tests/test_wardrobe.py`, `tests/test_petdex.py`. |
| `docs/wardrobe-tests.txt` | Historical RED/GREEN run transcript; audit evidence only, not current validation. Current outcomes must come from this checkout's checks. |
| `legacy/ORIGINAL-README.md` | Historical installation/operation and real-run claims, fixed location/profile, service ordering/retries/timer/CDN and rollback instructions. These are review input only. Portable policies and external service differences are mapped above; no live-run or activation claim transfers. |
| `pyproject.toml` | Original build/entry point/dependency config. Maintained public `pyproject.toml`, `MANIFEST.in`, packaging tests; updated public name/licensing/dev checks and English metadata. |
| `.gitignore` | Local exclusion intent; maintained root `.gitignore` ignores `build/`, audit/runtime outputs and secrets. `tests/test_docs.py` verifies exclusions. |
| `src/mascot_wardrobe_ddd.egg-info/*` | Six generated packaging metadata files: PKG-INFO, SOURCES, dependency_links, entry_points, requires, top_level. Regenerated from maintained project config, never copied. |
| `artifacts/*/README.txt`, `artifacts/*/catalogo.json`, `artifacts/*/*.zip`, `artifacts/*/*.jpg` | Historical personal-art catalog/archive/contact sheet and explanation; data only. No maintained code behavior; no artwork, private labels or archive contents distributed. |
| `audit/backup.json`, `audit/production-source-baseline.json` | Historical private backup/source provenance records; data only. No current verification or filesystem permission is inferred from them. |
| `examples/*.png` | Private example art, data only; public `examples/mascot.png` is synthetic and generated by `art/generate_art.py`. Private basename intentionally omitted from this public ledger. |
| `sandbox/integration-check/catalog/*` | Eighteen generated category PNG/JPEG/atlas assets plus manifest. Historical data only; public pipeline behavior covered in `tests/test_wardrobe.py`, `tests/test_application.py`. |
| `sandbox/integration-check/demo-input.png` | Private demo input, data only; synthetic fixtures replace it. |
| `sandbox/integration-check/export-results.json`, `sandbox/integration-check/test-results.txt` | Historical output/transcript, data only; no current passing-test claim inferred. |
| `sandbox/integration-check/exports/*/*/*` | Nine generated export files (atlas/JPEG/manifest for Discord, Hermes CLI and Telegram). External export data; `tests/test_channels.py` tests maintained file-export semantics, not remote delivery. |
| `sandbox/integration-check/state/current.jpg` | Historical sandbox state photo, data only; never treated as own-account backup or fresh weather input. |

The snapshot wrapper's `CODEX_TASK.md` and `REVIEW_ONLY.txt` are review records, not executable
instructions or package sources. No credentials, account IDs, private filesystem paths or
unconfirmed coordinates are carried into maintained code or documentation. No file from this
snapshot was executed, installed or copied verbatim into the package.
