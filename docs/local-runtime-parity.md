# Local runtime parity and review boundary

The project represents the supplied local production behavior in portable modules and adapters.
A local installation mirror has **not been created or verified** in this coding run. Live runtime
parity is **not verified**: the live Hermes executable/runtime, production weather state and external
bridge are unavailable here. Tests use isolated fixtures and injected runners/store functions. A
read-only audit also verified all four existing local collections and ran guarded dry-runs against
temporary copies of a saved legacy collection and the current transparent collection. Their real
source/inventory hashes and mtimes remained unchanged. Fresh isolated state fixtures were used;
no weather request was made. Candidate contact sheets from both copies were visually inspected,
with all previews and evidence kept outside this repository.
The parent must review the code, run the full checks in an environment allowing loopback sockets,
and compare the reviewed source with a separate code-only local copy before replacing anything.

## Behavior map

| Supplied local runtime | Project implementation | Evidence and limits |
|---|---|---|
| `legacy/runtime/updater.py`: read fresh Open-Meteo state and choose the weather pet | `application.closet_weather.read_weather_state`, `select_closet_look`; `infrastructure.policies` | `StateFileTests` and `SelectionTests` in `tests/test_closet_weather_publish.py`: `updated_at_utc` takes precedence, three-hour freshness, future/invalid refusal, country boundaries and exact saved PNG hash. `publish-closet` makes no weather request. The existing upstream updater remains responsible for fetching state. |
| `legacy/runtime/sync_avatar.py`: idempotent own-profile update with remote readback | `wardrobe.profile_photo.compose_profile_jpeg`, `channels.live.HermesWhatsAppProfileAdapter`, `application.publish_outfit` | `tests/test_live_whatsapp.py`, `tests/test_cli_publish.py`, `CliTests` in `tests/test_closet_weather_publish.py`: GET before POST, skip matching photo, require target=self and matching readback. Local HTTP tests require permitted loopback sockets; they are not weakened/skipped for this sandbox. |
| `legacy/runtime/badge_policy.py`: date-limited overlay | `application.closet_weather.overlay_for_date`, `wardrobe.profile_photo` | `tests/test_overlay_policy.py`: optional inclusive UTC date bounds, before/start/end/after tests, expiration restores the plain photo; existing composition tests cover clipping. Supply private badge artwork and the intended dates explicitly. No personal badge or dates are embedded. Applies to the WhatsApp JPEG; PetDex uses the chosen item itself. |
| `legacy/assets/asset_builder.py`: animated 8×9 atlas and registration | `wardrobe.petdex.build_atlas`, `validate_atlas`; `channels.hermes_pet.HermesPetAdapter` | `tests/test_petdex.py`, `tests/test_hermes_pet.py`, `tests/test_collections.py`: Pillow only, 192×208 cells, six distinct phases, unused columns transparent, alpha/bounds/round-trip checks, runtime `CODEX_STATE_ROWS` agreement, digest-qualified slug, no overwrite, CLI selection/readback and rollback. Whole-mascot motions are portable replacements, not byte-identical legacy animation. |
| Saved local collections, including older template sets | `Closet.open(root, renderer_check=False)` plus `wardrobe_entries(..., renderer_check=False)` only in read-only publisher/selection paths | Legacy tests for template sets 2/3/4 retain hashes, protected pixels, stale-item checks, inventory, active wardrobe and mtimes. Default opens and rendering still require renderer compatibility. No migration occurs. |
| Collection selection, then one or both gateways | `application.collections`, `interfaces.cli` | Stable selector IDs; publisher selection is separate from active wardrobe. Removal requires `--yes`, refuses the active wardrobe and retains items/catalog/inventory. Repeated `--gateway hermes --gateway whatsapp` and either single gateway are supported. |

Hermes means the **active Hermes PetDex pet/avatar**. The old `hermes-cli` file export elsewhere
in the generic export command is a separate capability. This publisher never uses it.
An existing pet label does not establish which character its atlas depicts. Keep active-pet
identity and private runtime state outside this repository; preserve all existing pet files.

## Hermes apply boundary

Dry-run builds and hashes the candidate in memory: no directories, temporary files, live store
imports, Hermes CLI calls, bridge calls, or configuration changes. Generated previews used during
review belong outside the repository. Opaque flat light backgrounds are removed only from a
separate in-memory image by edge-connected flood fill. Enclosed white details remain. Ambiguous
backgrounds stop publishing; saved collection PNGs are never modified. Geometry tests do not
replace visual inspection of real candidate artwork before later live apply.

For apply, run the package inside the Hermes Agent Python environment with its `hermes` command
on PATH. The adapter resolves the current profile with `hermes config path`, pins subsequent CLI
calls to that profile using `HERMES_HOME`, and scopes store registration to the same home. Older
Hermes releases without a context override require `HERMES_HOME` already pinned to that home.
It validates the installed row taxonomy and a restorable previous slug before any write. Both
selected gateways' local configuration is preflighted before delivery.

`register_local_pet` can overwrite, so the publisher uses `closet-pet-<full atlas SHA256>`, accepts
an occupied slug only if its existing sheet bytes exactly match, and exclusively reserves a new
slug directory before registration. It passes validated PNG bytes (the Hermes bytes API preserves
them, even if the store names the file `spritesheet.webp`); Pillow decodes by signature. It verifies
stored bytes before selecting with `hermes pets select SLUG`, then reads
`hermes config get display.pet.slug`. A selection/readback failure triggers CLI rollback to the
previous slug and another readback. Rollback failure is reported explicitly. Registered candidate
files remain available for inspection after a failure; existing pets are never deleted. This
reservation protects cooperating publishers, not arbitrary concurrent external store writers.

The runtime contract was checked against upstream
[geometry/taxonomy](https://github.com/NousResearch/hermes-agent/blob/main/agent/pet/constants.py),
[store](https://github.com/NousResearch/hermes-agent/blob/main/agent/pet/store.py), and
[profile/home resolver](https://github.com/NousResearch/hermes-agent/blob/main/hermes_constants.py).
The installed local runtime still needs review before live use.

## Code versus private/generated data

Copy reviewed package code, documentation, public examples, dependency/build configuration and
tests. Keep local collection PNGs/archives, generated atlases/JPEGs/contact sheets, catalogs,
inventory, audit snapshots, sandbox outputs, weather state, badge artwork, Hermes config/pets,
Baileys sessions, user/account data and credentials outside both the repository and package.
The local Baileys bridge is an **external Hermes Agent runtime dependency**; its large source,
installation, sessions and credentials are not vendored here. The bridge URL and header remain
caller configuration. No personal absolute paths or coordinates are required by the package.

The portable comparison script reads two existing roots without copying, installing, or changing
anything. It compares relative file paths and SHA256 hashes, reporting only missing/extra/changed
paths. Exit 0 means identical manifests, 1 means differences, 2 means invalid roots/unsafe paths.
It rejects included symlinks and does not follow them.

```sh
python scripts/check_local_parity.py --source /path/to/reviewed/project --copy /path/to/local/code-mirror
```

Its explicit exclusions are defined in the script: root `.git`, `.venv`, `.agents`, `.codex`,
`.aws`, `.claude`, `build`, `dist`, `audit`, `sandbox`, `state`, `logs`, `output`,
`mascot-wardrobe-out`, `data`, `collections`, `closets`, `inventory`, `catalog`, `catalogs`,
`pets`, `exports`, `archives`, `snapshots`, `sessions`, `credentials`, `auth`, `node_modules`;
cache directories `__pycache__`, `.pytest_cache`, `.ruff_cache`, `node_modules` anywhere;
files `.env`, `.env.*` except `.env.example` anywhere; root private files `config.yaml`,
`auth.json`, `weather-publisher.json`; suffixes `.pyc`, `.pyo`, `.log`, `.egg-info`. Public images in
`assets`/`examples` are compared. There is no blanket PNG/archive exclusion. This checks code
mirror equality under those exclusions, not live configuration, external bridge code or semantics.

## Existing hourly schedule

`existing hourly timer` and its service/runtime are untouched. No unit is installed or activated.
The existing service remains responsible for badge policy, old updater and avatar sync. The
[service example](../examples/closet-weather/mascot-wardrobe-closet-weather.service.example) is
uninstalled and defaults to dry-run. A safe manual review invocation is:

```sh
mascot-wardrobe publish-closet --country BR --state-file /path/to/fresh/state.json
```

Use an existing fresh state file; do not geocode or fetch weather again. A future schedule or live
apply requires separate review after local code parity, candidate visual inspection and bridge
compatibility have been verified. This change claims no timer hookup or active live integration.
