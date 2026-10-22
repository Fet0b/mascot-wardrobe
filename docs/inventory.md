# Persistent wardrobe inventory

The inventory keeps numbered garments independently of catalog output folders and closets.
Numbers begin at 1, increase monotonically and are never reused, including after removal.
Each item has its own name, style, palette, choice provenance and one or more suitability
ranges. A range is one of `very_cold`, `cold`, `mild`, `ambient`, `warm`, `very_hot`.
Suitability is descriptive: it never filters or ranks the inventory or restricts a default.

The core stores format labels and asset references without interpreting image bytes. The only
implemented asset adapter is static PNG, with optional JPEG preview and PNG atlas companions.
GIF, SVG and Lottie require their own adapters and are unsupported. Rendering preserves the
existing compositor's fit, mask, identity and protected-pixel guarantees.

## Data location and verification

Resolution order is global `--data-dir PATH`, then `MASCOT_WARDROBE_HOME`, then
`$XDG_DATA_HOME/mascot-wardrobe` when XDG_DATA_HOME is nonempty and absolute, then
`~/.local/share/mascot-wardrobe`. Relative XDG values are ignored. The root option goes before
the command: `mascot-wardrobe --data-dir /path/to/data inventory list`.

Under that home, `inventory/` holds versioned `inventory.json`, `assets/`, `backups/` and a
transaction lock. Existing `imports/` archives are never written. Transactions serialize,
validate referenced hashes and numbers, stage asset bytes and commit metadata atomically.
Changed metadata gets a numbered backup; an unchanged operation writes no backup. Unsupported
newer schemas are refused. Interrupted replacement recovers against the JSON commit point.
If metadata is missing while assets, backups or transaction evidence remain, opening the store
fails without reinitializing it. Preserve those files for recovery; numbering must never restart.

```sh
mascot-wardrobe inventory verify
mascot-wardrobe inventory list --json
mascot-wardrobe inventory list --range cold --json
```

Verification checks hashes, numbering and mappings and reports unreferenced asset files; it
does not silently delete them. List returns every garment in number order, including items
that do not list the requested range. `--range` adds only a suitability hint; no country is
required to list items.

## Import and creation

```sh
mascot-wardrobe inventory import-manifest /path/to/catalog/manifest.json
mascot-wardrobe inventory import-closet --root /path/to/closet
mascot-wardrobe inventory import-asset /path/to/coat.png --name "Blue Coat" \
  --range cold --range mild --style classic --palette "#2878b5" --format png
```

Manifest import copies all six recorded categories in manifest order, preserving file hashes,
request provenance and recorded renderer configuration without re-rendering old art. Malformed,
oversized or unsafe manifests, symlinks, changed hashes and invalid assets abort the batch.
Importing the same bytes again retains the same numbers. Imports never set defaults or active
selection. Missing old-manifest choices remain unknown or recorded as `default`, never guessed.
An existing portrait identity with different preview or atlas bytes is a collision and aborts
the entire import, including any new garments staged earlier in that batch.

`generate` registers its output by default; `generate --no-inventory` opts out. A repeat
generation imports as unchanged. `closet add`, `closet edit` and `closet piece` also register
their saved item, while library generation and `Closet` methods have no inventory side effects.
`closet piece` asks for suitability ranges, then style and palette separately, saves the piece
and shows its inventory number, then asks whether to create another. Bare numeric Celsius
answers retain their legacy meaning; named answers such as `cold,mild` use their first range
as the compositor template and have no numeric temperature.

## Closet identity and recovery

There is no closet UUID or closet schema change. An item id is unique across closets sharing
the same mascot and mask hashes. `closet add` checks availability before rendering; `piece`
skips taken suggested ids and asks again for a taken custom id. Removing a closet item leaves
its inventory garment and number intact. Use a different id to create another garment.

Unchanged bytes refresh changed closet metadata and merge suitability ranges without bumping
the garment version. Different bytes with the same identity are a collision; ordinary imports
never overwrite the existing garment. Explicit edits retain its number, increment its version
and append the previous asset hash to history only when `replaces` matches its current hash.

If a saved edit fails registration, the command keeps the closet output and prints a retry:

```sh
mascot-wardrobe inventory import-closet --root /path/to/closet --item coat --replaces PREVIOUS_SHA256
```

`--replaces` is valid only with `--item` and must match the registered current hash. After
`closet migrate`, import may use only the recorded `migrated_from.sha256` as its previous-hash
guard. No other implicit replacement exists. For failed generated registration, retry the
printed `inventory import-manifest` command; the catalog remains available. Errors exit 2.

Printed closet and piece retries include repeatable `--range` arguments so recovery retains
the chosen suitability ranges. Import ranges require `--item`; batch imports keep their own
template-derived ranges. A failed first edit of a pre-inventory closet also prints
`--allow-first-registration`, which requires both `--item` and `--replaces`: it may allocate
only when that identity is absent. An existing garment still requires the exact current hash,
so this option never bypasses the replacement guard.

## Choose and resolve defaults

```sh
mascot-wardrobe inventory ranges --country BR
mascot-wardrobe inventory change --country BR
mascot-wardrobe inventory mudar-peca --country BR
mascot-wardrobe inventory set-default --range cold --number 2
mascot-wardrobe inventory default --range cold
mascot-wardrobe inventory default --temperature 12 --country BR
mascot-wardrobe inventory select --temperature 12 --country BR
mascot-wardrobe inventory remove 3
```

Interactive `change` (alias `mudar-peca`) shows the profile boundaries, asks for one named range,
shows the entire numbered inventory and asks for the user's numeric garment number. Invalid
answers retry up to three times; end of input saves nothing. Empty inventories and nonterminal
input fail with exit 2; scripts use `set-default` instead. No automatic choice is made.

Defaults map category names to user-selected numbers, independently of country and suitability.
Country profiles classify temperatures and display boundaries; a threshold belongs to the
warmer category. `--unit F` accepts Fahrenheit. Temperatures must be finite and within the
existing -90 to 65 Celsius limits.

`default` resolves without changing active selection. `select` follows only the exact saved
mapping and sets the selected number as active. Without a mapping both return `no-default`
and exit 3; selection leaves inventory bytes and active state unchanged. There is no nearest,
random or ranked fallback. A default or active garment cannot be removed; otherwise its number
is retired permanently. `inventory select` changes no gateway, profile, photo or scheduler.

The generic [Hermes skill template](../examples/hermes-skill/mudar-peca/SKILL.md) orchestrates
only the user's range and number choices through documented CLI commands. Installation belongs
to the external orchestrator after verification. Publication to Bot Mode, Telegram or WhatsApp
requires a separate approved workflow; no inventory command contacts a channel.
