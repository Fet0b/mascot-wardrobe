# Local runtime parity and review boundary

The supplied review snapshot has been audited as input data. The file-by-file
[coverage ledger](local-copy-coverage.md) maps its package sources, tests, documentation,
three separate runtime modules, artwork preparation and external bridge components to
maintained behavior or a precise external boundary. Matching file hashes cannot establish
behavioral parity. No local installation mirror or live runtime parity is verified here.
Historical local run/visual-review claims in the snapshot are not evidence of this checkout.

Tests use synthetic assets, injected weather openers, fake Hermes runners/stores, and an
in-process fake profile bridge. Existing ephemeral loopback HTTP tests remain intact; a
sandbox socket restriction is a verification blocker, never grounds to skip/weaken them.
No private saved collection, active pet, live bridge, account, profile config, service or
production file is changed. No schedule is installed or activated.

## Policy and behavior map

| Local source | Maintained behavior and evidence |
|---|---|
| `legacy/runtime/updater.py` and `local-runtime/updater.py` | `infrastructure/legacy_weather.py` preserves the four legacy bands `<15`, `<22`, `<28`, otherwise hot, with Celsius -30..60, age 0..3h inclusive, no future allowance, and timezone/offset validation. `tests/test_legacy_weather.py`. The old fetch/retry/lock/state-writing service stays external; no fixed location or account slug is copied. |
| New collection weather policy | `application.closet_weather.select_closet_look` uses the selected saved wardrobe and the country profile's six categories; it does not use legacy bands. `read_weather_state` consumes numeric `temperature_c` and `updated_at_utc` (fallback `current_time_utc`), refusing stale/unsafe data. Publisher freshness is 3h with a 15-minute future tolerance. `tests/test_closet_weather_publish.py`, `tests/test_collections.py`. No new weather request or implicit location. |
| `legacy/runtime/badge_policy.py` and `local-runtime/badge_policy.py` | Generic `overlay_for_date` converts an aware instant into the explicitly supplied IANA zone before inclusive date comparison. `--overlay-timezone` defaults to UTC for compatibility; the local translation explicitly uses `America/Sao_Paulo`, start `0001-01-01`, end `2026-10-28`. Enabled at `2026-10-29T02:59:59Z`, disabled at `2026-10-29T03:00:00Z`. `tests/test_overlay_policy.py` tests both instants. Policy dates are caller configuration, never package defaults. |
| Badge output | Caller-supplied overlay is applied through shared portrait composition to both the WhatsApp JPEG and the PetDex atlas. Expiry restores both plain outputs; no overlay means none. No personal badge/vector/font/art ships. Unlike the local bulk copier, publishing does not modify all installed skins or expire them if fresh weather is unavailable. |
| `legacy/runtime/sync_avatar.py` and `local-runtime/sync_avatar.py` | `HermesWhatsAppProfileAdapter`: own-account GET/readback first; original observed photo backed up once before first POST; POST only on mismatch; up to three readback polls; atomic last-good `avatar-sync.json` only after verification; failure preserves previous bytes. A nonblocking lock serializes catalog staging and sync. `tests/test_avatar_safety.py`, `tests/test_live_whatsapp.py`. |
| `legacy/assets/asset_builder.py` | `wardrobe.petdex.build_atlas` and `validate_atlas`: 8×9 sheet, 192×208 cells, six distinct useful phases, two transparent columns, correct `CODEX_STATE_ROWS`, margins/alpha/round-trip checks. `tests/test_petdex.py`. Generic whole-mascot motions replace character-specific vector/CairoSVG animation; no exact visual/byte parity claimed. |
| Collections and gateways | `application.collections`, `interfaces.cli`: stable IDs, `collections list/select/remove`, repeated `--gateway hermes --gateway whatsapp`, separate persisted selection; never change `Closet.active` when publishing/selecting. `tests/test_collections.py`. Saved legacy template sets retain hash/stale/protected-pixel checks without renderer migration. |

## Apply safety and partial outcomes

Hermes means the **active Hermes PetDex pet/avatar**, separate from the `hermes-cli` file
export. Dry-run composes/hashes in memory with no filesystem writes, staging, live Hermes
imports, CLI calls or network requests. Only `--apply` can change a selected profile.

Hermes apply requires the Hermes Agent Python environment and CLI. `HermesPetAdapter`
resolves the active profile via `hermes config path`, pins CLI/store calls to it, validates
runtime `CODEX_STATE_ROWS`, and requires a restorable previous slug. New sheets use a full
digest-qualified slug and exclusive directory reservation; occupied differing sheets are
refused. It verifies registration bytes, selection and readback. Selection failure restores
and verifies the previous slug. Existing pet files are preserved. These are tested interfaces;
the installed runtime, TUI repaint and candidate personal artwork still require later review.

For combined Hermes + WhatsApp apply, Hermes preflight and WhatsApp catalog/state/bridge
GET readback complete before either profile changes. The bridge needs the candidate JPEG
in its approved catalog to compare, so apply may stage that JPEG and create lock/state
directories during preflight; preflight failure can leave these reviewable files. No profile
has changed at that point. The built-in WhatsApp lock remains held across both deliveries.
Sync reads the own photo again before POST. Its state defaults to `<approved-catalog>/.avatar-sync`
in the CLI; Python callers must supply a private state directory. Keep one state/backup per
connected account and reuse it for subsequent runs. Do not repoint it to a different account.

Each delivery failure is reported per gateway. Hermes failure does not hide a successful
WhatsApp delivery. WhatsApp failure after Hermes success triggers Hermes rollback where the
adapter supports it, reporting `rolled-back` or `rollback-failed`; a rollback error cannot be
reported as success. `tests/test_combined_apply.py` and `tests/test_hermes_pet.py` cover this.
The bridge's slug-only POST interface cannot restore arbitrary backup bytes; a failed upload
or unverified readback may have changed the remote photo. Last-good state remains unchanged,
with the original backup retained for separately reviewed restoration. Multi-target apply is
not an atomic remote transaction.

## External bridge and legacy service boundary

The Hermes/Baileys bridge stays external, including bot routes, allowlist, owner-message gate,
authentication, sessions and dependencies. The package uses exactly GET
`/profile-picture?slug=...` and POST `/profile-picture` with JSON `{slug}`, plus a configured
header. It accepts only `target=self`; callers cannot select another recipient. Requests have
no browser Origin and no redirects/proxies. Header values and remote error bodies never enter
logs or reported errors. No credentials or account identifiers are stored in publisher selection.

The bridge performs actual CDN readback and perceptual comparison to tolerate WhatsApp
recompression. The package validates the returned target, boolean match and remote bytes/hash;
it trusts the configured bridge's comparator. POST acknowledgement alone is **not verified**.
Fake-bridge tests do not verify Baileys, CDN comparison thresholds, account changes or live parity.
A missing observed photo without an existing backup fails closed before first POST.

The old updater's fixed launcher/profile, three weather request attempts, updater lock, weather
state writing, badge bulk installation, history/gallery and service chaining stay external.
The legacy chain can expire badges and sync a previous pet even if fetching weather fails;
`publish-closet` instead requires fresh valid weather before delivering. It does not provide that
failure fallback or mutate all installed pets. No personal paths, coordinates, labels or art are
copied. See every source/test mapping in [local-copy-coverage.md](local-copy-coverage.md).

The `existing hourly timer` described by historical input is untouched. Current location and
cadence remain unresolved. Any future integration must explicitly **replace or disable the old
avatar post-hook**, otherwise it may overwrite the new WhatsApp photo. The uninstalled
[service example](../examples/closet-weather/mascot-wardrobe-closet-weather.service.example)
shows `ExecStartPost=` reset semantics and a dry-run command. Retaining the old updater as a
state producer also retains its active-pet effects unless separately changed/reviewed. Activation,
profile changes and schedule choices need separate user approval; this change does none of them.

## Code mirror comparison and private data

`scripts/check_local_parity.py` is an optional read-only code mirror comparison:

```sh
python scripts/check_local_parity.py --source /path/to/reviewed/project --copy /path/to/local/code-mirror
```

It reports relative missing/extra/changed file paths only, rejecting included symlinks. Exit 0
means equal included manifests, 1 means differences, 2 means invalid inputs. Exclusions are
explicit in the script: private root runtime/data/configuration directories, caches, environment
files and generated bytecode/log/egg-info suffixes. Public code/docs/config examples/images are
included. It establishes code equality under those exclusions, not behavior or live integration.

The review snapshot is ignored under `build/` and pruned from distributions. Generated assets,
audit records, local images/archives, runtime state, credentials, Baileys sessions, personal
collections and installed Hermes config/pets are input data or external components, never public
package code. The coverage ledger accounts for them without importing them.
