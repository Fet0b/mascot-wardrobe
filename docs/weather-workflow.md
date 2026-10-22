# Weather-first workflow

Choosing the look from the weather is the primary use of mascot-wardrobe. You keep a **named
saved wardrobe** in an identity-locked [closet](closet.md); the current temperature picks one of
its looks; a profile-photo publisher can then deliver that exact look. The older manifest
workflow (`generate` / `select` / `publish`) stays available and unchanged.

## Workflow

1. **Build the wardrobe once.** `closet init`, `closet add` (one item per weather category),
   `closet save <wardrobe> --items <item-ids>`. Items are rendered locally from your own mascot and mask.
2. **Read the temperature.** Either `--temperature` (explicit, Celsius or `--unit F`) or
   `--state-file`: the JSON your existing weather updater already writes.
3. **Classify.** The country profile (`profiles.json`, or `--profiles`) maps the temperature to
   one of six categories. No threshold lives anywhere else.
4. **Select.** The wardrobe item whose `template` is that category. Exactly one per category is
   required; otherwise the command fails closed.
5. **Plan or apply.** `publish-closet` renders the saved item as a profile JPEG and prints the
   plan. Only `--apply` changes the WhatsApp profile photo or selects the active Hermes PetDex pet/avatar.

```sh
# Dry-run (the default): prints category, item id, slug, hash and a planned WhatsApp channel.
# No writes, no requests. Needs MASCOT_WARDROBE_WHATSAPP_BRIDGE_HEADER in the environment.
mascot-wardrobe publish-closet --closet-root closet --wardrobe my-looks --country BR \
  --state-file /path/to/weather/state.json \
  --whatsapp-catalog-dir /path/to/bridge/approved-photos \
  --whatsapp-bridge-url http://127.0.0.1:3000
```

`MASCOT_WARDROBE_WHATSAPP_BRIDGE_HEADER` (`Name: value`, as your bridge requires) must be set
from a private environment file kept outside the repository; no real header or token appears in
these docs. The catalog directory, bridge URL and header are required together: omit them and
the dry-run plans only the selection, with no WhatsApp channel.

`--apply` is deliberately absent above. It is the separate, explicit live-change flag: appended
to the same command it stages the JPEG and changes the WhatsApp profile photo. Without it
nothing is written or requested.

## Saved collections and publisher selection

Discover named wardrobes across the existing default data home, `~/.local/share/mascot-wardrobe`:

```sh
mascot-wardrobe collections list
mascot-wardrobe collections list --json
mascot-wardrobe collections select SELECTOR_ID --gateway hermes --gateway whatsapp
# Either single destination is also valid:
mascot-wardrobe collections select SELECTOR_ID --gateway hermes
mascot-wardrobe collections select SELECTOR_ID --gateway whatsapp
# Removes only a non-active named entry and retains assets/catalog/inventory:
mascot-wardrobe collections remove SELECTOR_ID --yes
```

Replace `SELECTOR_ID` with the stable ID printed by `list`, which also shows the wardrobe name,
closet root, item count and active status within that closet. Same-named wardrobes in different
closets have different IDs. IDs depend on relative closet path and wardrobe name, so adding or
removing other wardrobes does not renumber them. Moving a closet changes its IDs.

The data home follows the existing inventory resolution order: global `--data-dir PATH`, then
`MASCOT_WARDROBE_HOME`, then absolute `XDG_DATA_HOME/mascot-wardrobe`, then the default above.
For example, `mascot-wardrobe --data-dir /path/to/data collections list`. Use the same data home
when publishing. Listing and selection reads never create an inventory or follow symlinked closets.
Listing reads schema-1 wardrobe metadata, including legacy template-set-2/3/4 closets, without
opening the renderer or requiring migration. A legacy closet does not prevent other saved
collections from being listed. No saved files are changed.
The rows represent named wardrobes: a closet containing only loose items and no saved wardrobe
entry contributes no rows.

Selection stores schema version, selector ID, absolute closet root, wardrobe name and gateways in
`<data-home>/weather-publisher.json`, using atomic replacement. It never changes `Closet.active`
or inventory. On an interactive terminal, `mascot-wardrobe collections select` first asks for a
collection ID and then one or more gateways; enter `hermes,whatsapp`, or either name. An explicit
ID skips the collection prompt. Explicit gateway flags skip the gateway prompt. Scripts and
global `--no-input` require an ID plus explicit repeated `--gateway` flags; no gateway is assumed.

`remove SELECTOR_ID --yes` removes only the named wardrobe entry from its owning closet manifest
and refuses the active wardrobe. **All item assets, item records, inventory and catalog files are
retained.** It neither deletes a closet nor cleans up retained files. If the removed entry was the
publisher's selection, subsequent publishing fails closed until you choose a valid collection.
Removal works on legacy manifests too, preserving their schema, renderer and all other fields.
No command migrates a closet. Selecting/publishing explicitly skips only renderer-version
compatibility: saved hashes, protected pixels and stale-item checks still apply. Legacy selection
and publishing never modify collection assets, inventory or timestamps.

The supported gateways have different effects:

| Gateway | Target | Effect with `--apply` |
|---|---|---|
| `hermes` | active local Hermes PetDex pet/avatar | Builds an 8×9 atlas from the exact saved PNG, registers a digest-qualified pet and selects/verifies it via Hermes CLI. |
| `whatsapp` | existing WhatsApp profile adapter | Stages and changes the connected account's profile photo through its configured bridge. |

Use the saved choice by omitting **both** `--closet-root` and `--wardrobe`:

```sh
# Both selected gateways are planned; deterministic, no filesystem writes or network calls.
mascot-wardrobe publish-closet --country BR --state-file /path/to/weather/state.json
# weather-run is an equivalent alias:
mascot-wardrobe weather-run --country BR --state-file /path/to/weather/state.json

# Explicit opt-in: active Hermes PetDex pet plus WhatsApp profile-photo change.
# The private bridge header comes from MASCOT_WARDROBE_WHATSAPP_BRIDGE_HEADER.
mascot-wardrobe publish-closet --country BR --state-file /path/to/weather/state.json \
  --whatsapp-catalog-dir /path/to/bridge/approved-photos \
  --whatsapp-bridge-url http://127.0.0.1:3000 --apply

# Active Hermes PetDex pet only, with no WhatsApp configuration or bridge/network calls:
mascot-wardrobe collections select SELECTOR_ID --gateway hermes
mascot-wardrobe publish-closet --country BR --state-file /path/to/weather/state.json --apply
```

Hermes dry-run reports the atlas hash and intended `closet-pet-<SHA256>` slug entirely in memory.
Apply requires the Hermes Agent Python environment and `hermes` on PATH. The adapter resolves
the active profile dynamically, checks runtime row taxonomy, rejects occupied slugs with different
sheet bytes and preserves existing pets. It registers only a new digest-qualified pet and then
selects it with `hermes pets select SLUG`, verifying `hermes config get display.pet.slug`.
Selection/readback failure reselects and verifies the previous slug; rollback failure is explicit.
An identical repeat reuses the sheet and reports `unchanged` when already selected. No config.yaml
is edited directly. See [local-runtime-parity.md](local-runtime-parity.md) for prerequisites,
visual review, private-data boundaries and limitations.

The saved selection contains no credentials. Selected WhatsApp can be planned without settings;
applying it requires the complete existing WhatsApp configuration. Hermes-only apply requires no
WhatsApp settings. All local configuration preflight precedes writes. Each selected gateway is
attempted after preflight; a delivery failure is reported per gateway and exits 1, so partial
delivery is possible.

Missing, malformed, stale or unsafe selections and missing/stale weather state fail with exit 2
before any gateway writes. No active wardrobe is used as a fallback. The explicit manual
`--temperature` path remains available instead of `--state-file`. Supplying both explicit
`--closet-root` and `--wardrobe` overrides the saved choice and keeps the original WhatsApp flow;
supplying just one fails. Repeated `--gateway hermes --gateway whatsapp` overrides gateways for
that run; either single flag works too. All publishing remains dry-run unless `--apply` is present.

Optional badge artwork can be restricted to inclusive UTC dates with
`--overlay /path/to/badge.png --overlay-start-date 2026-10-21 --overlay-end-date 2026-10-22`.
Dates here are illustrative; supply your own policy. Outside the window the plain JPEG is used.
The PetDex atlas always uses the exact selected item, without the profile overlay.

## Data model

| Piece | Where | Contract |
|---|---|---|
| Wardrobe | `closet.json` | Named list of items pinned by version and sha256; goes stale rather than silently changing. |
| Publisher selection | `<data-home>/weather-publisher.json` | Separate atomic closet/name + gateways choice; does not activate a wardrobe. |
| Item | `items/<id>.png` | Exact saved PNG; its `template` is its weather category. Never regenerated by publishing. |
| Country profile | `profiles.json` | Five ascending Celsius boundaries give six categories. |
| Weather state | updater JSON | Numeric `temperature_c` and a timezone-aware `updated_at_utc` (`current_time_utc` only as a fallback). Refused if older than 3 hours, more than 15 minutes in the future, naive, or not a regular file. |
| Profile image | in memory, then staged on `--apply` | 1024x1024 JPEG on `#102b32`, mascot kept inside the circular-safe area; optional external overlay, clipped. |

## MVP boundary

In scope: selecting from a saved wardrobe by weather, a deterministic profile JPEG, dry-run by
default, collection management, a separate gateway selection, active local Hermes PetDex selection, and an
opt-in WhatsApp own-profile update with the existing readback and idempotence.

Out of scope, by design: any weather API request or coordinates in `publish-closet`; Telegram;
switching the active wardrobe; any closet or inventory write while publishing; creating or installing a timer or
service; shipping personal artwork or badge art (an overlay is always supplied by you).

## Scheduler hookup is opt-in

This repository never installs or enables a scheduler. When you decide to automate, **reuse the
hourly job you already run** for the weather updater after separate parity review. The
`examples/closet-weather/` service command is a dry-run; no existing unit is changed here.
Do not add a second timer for the same purpose; the state file is the hand-off, so one cadence serves both. Until you wire it yourself, nothing runs.
Remove `--apply` to return to a dry-run.

## Backward compatibility

`generate`, `select`, `demo`/`sandbox`, `publish` (manifest based, with its optional Telegram
target), `closet` and `inventory` keep their arguments, output formats and exit codes. The
`manifest.json` and `closet.json` (schema 1) formats are unchanged. `publish-closet` is additive.

## Acceptance contract

- Boundary temperatures pick exactly the category the country profile gives.
- A missing or duplicate category, an unknown, stale or unsafe wardrobe, an invalid temperature,
  or a stale or malformed state file exits 2 before any write.
- Dry-run performs no file write and no network request, and reports category, item id, slug and
  intended action.
- `--apply` stages the JPEG in the approved directory and syncs through the existing adapter; a
  repeat run reports `unchanged`; a bridge failure exits 1.
- The closet, inventory and active wardrobe are byte-identical before and after any run.
  The explicit `collections remove --yes` management action changes only its named manifest entry.

These are enforced by `tests/test_closet_weather_publish.py` and `tests/test_collections.py`.
Usage details and rollback are in
[closet.md](closet.md); scheduling templates are in [scheduler.md](scheduler.md).
