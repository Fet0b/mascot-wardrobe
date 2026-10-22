# Persistent numbered wardrobe inventory, per-range defaults and `mudar-peca`

## Context

Mascot Wardrobe today generates six outfits into a throwaway catalog directory (`generate`) or keeps items inside one identity-locked closet (`closet`). Nothing is a persistent, numbered inventory that outlives an output folder, and nothing records which garment the user wants for each temperature range. The approved scope (see the requirements file handed to this plan) turns the project into a persistent inventory and temperature-based wardrobe manager. The domain rules (inventory, ranges, selection, persistence) must not depend on art style, body shape or file format. Format-specific work (rendering, validating and publishing) sits behind adapters. The current PNG/mask compositor stays a supported adapter with its fit, mask, identity and protected-pixel guarantees untouched.

The project's existing "temperature ranges" are the six `Category` values (`very_cold` ... `very_hot`) whose Celsius boundaries come from `CountryProfile.thresholds_c` (`domain/models.py`, `infrastructure/profiles.json`). The inventory keys defaults by the category name, and the country profile is only used to classify a Celsius value and to display each range's boundaries. No numeric mapping has to be invented.

Baseline: branch `feat/style-parameters-fit` at `8f47764` (local; `origin/feat/style-parameters-fit` is still `daed14d`), full suite 331 passed. The branch already contains `closet piece` (one item at a time, per-item style and palette) and distinct streetwear templates; this plan builds on them and does not rewrite those commits.

## Approach

1. A pure domain module holds the inventory model. An infrastructure store persists it as versioned JSON outside the repository. A small format-adapter registry validates assets; only `png` (static PNG, same limits as the compositor) is registered, and any other format fails with "format X is not supported".
2. An application layer exposes use cases: register generated or closet items, import a manifest catalog, import a closet, import a single asset, set and resolve defaults, select by temperature.
3. The CLI gets an `inventory` command group. `inventory change` (alias `mudar-peca`) is the interactive "change piece" workflow, and every step also has a non-interactive form so the Hermes skill can orchestrate it without business logic of its own.
4. `generate`, `closet add`, `closet edit` and `closet piece` register their results automatically in the inventory. `closet piece` asks for the intended temperature range(s) and, as before, for style and palette per item, then asks about another piece.
5. A generic skill template under `examples/` is the thin orchestrator; installing it into the user's Hermes profile is done afterwards by the orchestrator through its own skill tooling and is not part of this implementation.

No remote inference, no network, no gateway or profile-photo mutation, no scheduler installation. Nothing here claims token savings.

## Data model (domain, no I/O, no Pillow, no format knowledge)

New file `src/mascot_wardrobe/domain/inventory.py`:

```python
@dataclass(frozen=True)
class AssetFile:            # one file of an asset, path relative to the inventory root
    role: str               # "portrait", "preview", "atlas"; opaque to the domain
    path: str
    sha256: str

@dataclass(frozen=True)
class AssetRef:
    format: str             # "png" today; the domain never interprets it
    files: tuple[AssetFile, ...]

@dataclass(frozen=True)
class Garment:
    number: int             # stable, visible, never reassigned
    name: str               # 1 line, same limits as closet names
    style: str | None
    style_choice: str | None
    palette: tuple[str, ...]
    palette_choice: str | None
    ranges: tuple[str, ...] # Category values; descriptive only, never a restriction
    asset: AssetRef
    version: int            # bumps when an edit replaces the asset; the number does not change
    origin: dict            # JSON provenance: {"kind": "manifest"|"closet"|"asset"|"generated", ...}

@dataclass(frozen=True)
class Inventory:
    schema_version: int
    next_number: int        # monotonic counter, persisted; deletion never lowers it
    garments: tuple[Garment, ...]
    retired: tuple[int, ...]            # numbers of removed garments, kept so they are never reused
    defaults: Mapping[str, int]         # Category value -> garment number (user chosen)
    active: int | None                  # last garment applied by a mapped selection
```

Pure operations (all return a new `Inventory`, raise `ValueError` on misuse): `add(garment_fields) -> (Inventory, Garment)` (assigns `next_number`, increments), `replace_asset(number, asset)` (version +1), `remove(number)` (refused while the number is a default or the active garment; appends to `retired`), `set_default(range, number)` (range must be a `Category` value, number must exist, ranges metadata is NOT checked), `default_for(range) -> int | None`, `describe(number)`, `find_by_origin_key(key) -> Garment | None`, `closet_item_id_free(mascot_sha256, mask_sha256, item_id) -> bool` (read-only; see the collision rule below). Validation of names (copy the `closet._display_name` rules: one line, at most `MAX_NAME_LENGTH = 60` characters; `closet.py` imports Pillow, so the domain must not import it), palette (copy the `#[0-9a-fA-F]{6}` rule and the 1-6 colour count from `wardrobe.styles.validate_palette`, as `domain/models.py` already does for profiles; `wardrobe/styles.py` must not be imported by the domain) and range names lives here. The domain must import nothing from other packages except `domain.models.Category`.

## Persistence (infrastructure)

New file `src/mascot_wardrobe/infrastructure/inventory_store.py`:

- `data_home(override=None) -> Path`: `override` (CLI `--data-dir`), else env `MASCOT_WARDROBE_HOME`, else `$XDG_DATA_HOME/mascot-wardrobe` (only when `XDG_DATA_HOME` is set, non-empty and absolute; a relative value is ignored, per the XDG spec), else `~/.local/share/mascot-wardrobe`. Resolve it lazily at command run time, not when the parser is built, so tests that change the environment see the new value. The inventory lives in `<home>/inventory/` (`inventory.json`, `assets/`, `backups/`, `inventory.lock`). Reject symlinked roots and `..` like `safe_output`. The existing import archive area `<home>/imports/` is never written.
- `InventoryStore.open(home) -> InventoryStore` creates an empty v1 inventory on first use. `store.transaction()` is a context manager: takes an exclusive `fcntl.flock` on `inventory.lock`, loads and validates `inventory.json` (schema version, types, number/retired/default consistency, every asset file present with matching sha256), lets the caller stage new asset files, and commits by atomic write (temp file, fsync, `os.replace`) of `inventory.json`, which is the commit point. A failed or aborted transaction leaves `inventory.json` and all previously registered assets byte-identical; staged assets are written to temp names and removed on failure (orphans after a crash are ignored and reported by `verify`).
- Versioning and backup: `SCHEMA_VERSION = 1`. Before every committing transaction that changes an existing `inventory.json`, copy it to `backups/inventory-<n>.json` (never overwrite an existing backup). A file with a higher schema than the code supports is refused with a clear message; a lower one goes through an ordered `MIGRATIONS` table (empty for now, tested with an injected fake v0 -> v1 step so the mechanism is real, not claimed).
- Assets are copied into `assets/<number>/<role>.<ext>` (new version replaces the previous file of the same number only after the new bytes are hashed and staged; the previous hash stays in the garment's `origin.history`).

Format adapters, new file `src/mascot_wardrobe/infrastructure/asset_formats.py`: a `Protocol` `AssetFormat` with `name`, `extensions`, `validate(path) -> dict` (size, mode) and a registry `FORMATS = {"png": PngFormat()}`. `PngFormat.validate` reuses the compositor's rules (static PNG, `Image.open`, `n_frames == 1`, at most 16,777,216 pixels, no decompression bomb) and returns metadata. Optional companion roles (`preview` JPEG, `atlas` PNG) are validated by extension and decoded the same way. `get_format(name)` raises `ValueError("format 'x' is not supported; supported: png")`. This is the only module allowed to import Pillow for inventory purposes.

## Application use cases

New file `src/mascot_wardrobe/application/inventory.py` (re-exported from `application/__init__.py` where the CLI imports them). Import-cycle rule: `application` is a package whose `__init__.py` holds `safe_output`, `select_outfit` and the rest. If `application/inventory.py` imports from `mascot_wardrobe.application`, the re-export in `__init__.py` must be placed at the end of the module, after those definitions. The alternative is to move `safe_output` into a helper module that both files import. Either way, `import mascot_wardrobe.application` must not hit a partially initialised module.

- `register_manifest(manifest_path, store) -> dict`: the idempotent importer for existing generated catalogs (`manifest.json` plus its files). Safety: manifest at most 262144 bytes; `outfits` is a list of exactly six objects with distinct valid categories; each `slug` matches `[a-z0-9][a-z0-9-]{0,100}` and the file names equal `<slug>.png`, `.jpg`, `-atlas.png` (same rule as `select_outfit`); no symlinks, files resolve inside the manifest directory; manifest keys map to asset roles as `png -> portrait`, `jpg -> preview`, `atlas -> atlas` (`WardrobeAgent.generate` writes `outfit["sha256"]` keyed by `png`/`jpg`/`atlas`, not by role); every file's sha256 must equal `outfit["sha256"][key]` when present (mismatch aborts the whole import with nothing written); each PNG passes the PNG adapter. Each outfit becomes one garment with `ranges = (category,)`, `name` derived from the slug, `style`, `palette` and their provenance read from `request` (missing keys on old manifests read as `None`/`"default"`, never guessed), `origin = {"kind": "manifest", "mascot_id", "source_sha256" (of the mascot), "category", "country_code", "thresholds_c", "manifest_sha256", "png_sha256"}`. Defaults are NOT created: importing never sets a range default. Returns `{"imported": [...], "skipped": [...]}` with numbers.
- `register_closet(closet, store)` / `register_closet_item(closet, item_id, store, *, ranges=(), replaces=None)` (`register_closet` registers every item in one transaction and never passes `replaces`): copies the item's saved PNG (`closet.item_path(item_id)`); the record comes from the public `closet.items()[item_id]` (name, template, style, style_choice, palette, palette_choice, version, sha256). `Closet` has no public accessor for the mascot or mask hash (the CLI's `closet init` reads `closet._data["mascot"]["sha256"]`). Add read-only `mascot_sha256` / `mask_sha256` properties to `Closet`; this is not a schema change. `origin = {"kind": "closet", "mascot_sha256", "mask_sha256", "item": item_id, "history": []}`. `ranges = (template,) + ranges` (deduplicated, template first). Closets do not store suitability ranges, so on an in-place update the garment's existing ranges are kept and merged with the current template and any new `ranges`. Without this, a later `closet edit` without `--range` would silently drop ranges given at `closet add --range`. Called by `closet add`, `closet edit` and `closet piece` after the closet commit succeeded. `closet migrate` does not register automatically (it re-renders every item). A later `inventory import-closet` registers new items and refreshes metadata of unchanged ones; an item whose bytes differ from its registered garment is a collision (see below), never an implicit in-place update.
- `register_asset(path, store, *, name, ranges, style=None, palette=None, fmt="png") -> dict`: manual import of one existing asset; validated by the adapter chosen from `fmt`; `origin = {"kind": "asset", "source_sha256"}`.
- Idempotency and collision rule (single rule for all importers). A garment is identified by its `origin` key (manifest: `png_sha256`; closet: `mascot_sha256 + mask_sha256 + item`; asset: `source_sha256`). There is no per-closet UUID and no `closet.json` schema change. Instead, an item id is unique across every closet that shares the same mascot sha256 and mask sha256, and the inventory enforces it:
  - Same key and identical asset bytes: returns the existing number with `status: "unchanged"`. For the closet kind, identical bytes with changed metadata refresh the garment's name, style, palette, choice provenance and merged ranges without a version bump (`status: "updated"`), which covers a label-only or choice-only `closet edit`.
  - Same key and DIFFERENT asset bytes through any implicit path (`inventory import-closet`, a repeated `register_closet_item`, a second closet that reused the id): fail the whole operation atomically with a clear `collision` error naming the garment number, the item id and the rule ("item id X is already registered for this mascot and mask as garment #N from another closet or an earlier state; choose a different item id"). The first garment's record, version, number and files stay byte-identical. Nothing is ever replaced silently.
  - Explicit edit path: `register_closet_item(closet, item_id, store, *, replaces=<previous sha256>)` is used only by `closet edit` and by the revision step of `closet piece`. The caller reads the item's sha256 before the edit and passes it as `replaces`; the update (`replace_asset`, version +1, same number, previous hash appended to `origin.history`) is allowed only when the garment's current asset sha256 equals `replaces`. If it does not (the garment belongs to another closet's history), the call fails with the same collision error. An ordinary re-import never passes `replaces`, so an ambiguous duplicate import can never overwrite.
  - Recovery paths (decided in this plan: narrow and explicit). (1) After `closet migrate` re-renders items, `inventory import-closet` treats an item's recorded `migrated_from.sha256` as an implicit `replaces`, and only that: the garment is updated in place (version +1, same number) when its current asset sha256 equals that recorded previous hash. (2) When a `closet edit` or piece revision commits to the closet but the inventory registration fails, the failure message prints the exact retry `mascot-wardrobe inventory import-closet --root DIR --item ID --replaces SHA`, where `SHA` is the item's previous sha256; `--item` limits the import to one item and `--replaces` is honoured only together with `--item` and only when it equals the garment's current hash, otherwise the collision error applies. No other implicit update exists; items that fit neither case need a new id.
  - `closet add` and the new-item step of `closet piece` check the domain's `inventory.closet_item_id_free(mascot_sha256, mask_sha256, item_id)` (exposed through an application helper that reads the store under its lock) BEFORE rendering; with `store=None` (library use of `run_piece_session`) the check is skipped: if the key is registered (another closet with the same mascot and mask, or an item removed from this closet whose garment remains), the command refuses with the collision error and writes nothing to the closet. `closet piece` also skips taken ids when proposing the default id and re-asks on a taken custom id. Re-adding a removed id therefore needs a new id; the garment keeps its number.
  - Manifest and asset kinds are content-addressed, so the same key always means the same bytes and the collision case cannot arise.
  Importing the same collection twice therefore never creates duplicates and never changes numbers, defaults or active garment.
- `set_default(store, range, number)`, `inventory_view(store, range=None) -> list` (no country needed: ranges are category names), `inventory_ranges(country, profiles) -> list` (the six ranges with their Celsius boundaries), `resolve_default(store, country, temperature, unit, profiles) -> dict`, `select_from_inventory(...)`.
- `inventory_view` returns the ENTIRE inventory ordered by number, each row `{number, name, style, palette, ranges, format, version, suitable: bool}` where `suitable` is only the hint "this range is in the item's suitability ranges" and rows with `suitable == False` are still returned. It never sorts by suitability, never truncates, and takes the requested range only to compute the hint.
- `select_from_inventory`: classifies the temperature with `profile.classify` (same boundary rule as `select_outfit`), looks up `defaults[category]`. With a mapping it returns `{"status": "selected", "number", "category", "temperature_c", "country_code", "assets": {...}}` (the asset paths absolute inside the store, shaped so a later `publish` wiring can reuse them), and sets `active` to that number in the store. Without a mapping it returns `{"status": "no-default", "category", "temperature_c"}` and changes nothing (no `active` change, no files written). It never picks a "closest" range, random item or ranked item. It never calls a channel.

## CLI

Edit `src/mascot_wardrobe/interfaces/cli.py`:

- Global optional `--data-dir PATH` on the root parser, like `--no-input`, so it goes before the subcommand (`mascot-wardrobe --data-dir P inventory list`). Its default is `None`, and `data_home(args.data_dir)` resolves it at run time.
- Dispatch: `main` catches `ValueError`/`OSError`/`RuntimeError` and exits 2. Exit code 3 (`no-default`) needs an explicit branch that prints the JSON result and returns 3. Register `inventory change` with `aliases=["mudar-peca"]`. argparse stores the name the user typed in `dest`, so dispatch must accept both `change` and `mudar-peca`. The alias stays ASCII: `tests/test_english.py` rejects accented characters in `src/`, so "peça" must never appear in source strings.
- `inventory list [--range R] [--json]`: prints the whole numbered inventory; with `--range` the suitability hint column is added. No `--country`: the skill calls `inventory list --range <range> --json` without one.
- `inventory ranges --country XX [--profiles P]`: the six ranges with their Celsius boundaries from the profile (so a user or skill can show them).
- `inventory import-manifest MANIFEST`, `inventory import-closet --root DIR [--item ID --replaces SHA]` (`--replaces` only with `--item`, see the recovery paths under the collision rule), `inventory import-asset FILE --name N --range R [--range R2] [--style S] [--palette ...] [--format png]`.
- `inventory set-default --range R --number N` (non-interactive contract used by the skill).
- `inventory change --country XX [--profiles P]` with alias `mudar-peca`: interactive (needs a terminal and no `--no-input`; otherwise exit 2 with guidance to use `set-default`, as `closet piece` does today). Flow: show the ranges with their thresholds, ask which named range (`very_cold` ... `very_hot`, shown with the thresholds), print the entire numbered inventory (every item, suitability only as a hint), ask for the numeric garment number, persist `range -> number`, report the saved mapping. Invalid answers re-ask up to 3 times, end of input saves nothing.
- `inventory default (--range R | --temperature T [--unit C|F] --country XX [--profiles P])`: resolve and print the mapping. Exit 0 when a default exists; exit 3 with `{"status": "no-default"}` when none exists, so a script can never mistake it for success.
- `inventory select --country XX [--profiles P] --temperature T [--unit C|F]`: `select_from_inventory`; exit 3 on no-default.
- `inventory remove NUMBER` (refused while the number is a default or active garment) and `inventory verify` (hashes, numbering, mapping consistency, orphan report).
- `generate`: after `prepare_wardrobe` succeeds, register the manifest in the inventory by default (`--no-inventory` opts out for hermetic use). If registration fails, keep the generated catalog, print the error to stderr and exit 2 with the retry command (`inventory import-manifest ...`). On success the existing output keys (`manifest`, `generator`, `remote_inference`, `style`, `palette`, `style_choice`, `palette_choice`) stay unchanged, and one `inventory` key is added holding the importer result. Re-running `generate` on an existing catalog returns the existing manifest, so registration reports `unchanged`.
- `closet add/edit`: `closet add` first checks `closet_item_id_free` and refuses a taken id before rendering; `closet edit` reads the item's sha256 before the edit and passes it as `replaces`. Both register after the closet commit (same failure rule). Add `--range R` (repeatable) to record extra suitability ranges; default `ranges = (template,)`.
- `closet piece`: the temperature question keeps its prompt label (`temperature> `) and its existing numeric behaviour: a bare number such as `12` is a Celsius value classified through `profile.classify`, exactly as today. In addition the question accepts the named project ranges (`very_cold`, `cold`, `mild`, `ambient`, `warm`, `very_hot`), alone or comma-separated (`cold,mild`), so the user can pick ranges without a Celsius value; the six names are listed with their thresholds in the question text. No numeric range indices and no unit suffix exist. The first range is the garment template, all are recorded as suitability ranges and in the item description; a numeric answer yields exactly one range and records `temperature_c`, a named answer records `temperature_c = None`. Mixing a number and names in one answer is rejected with a clear message. Style and palette are still asked separately and never inherited; the revision flow and the "create another piece?" loop stay. After each piece is saved it is registered in the inventory and its inventory number is shown with the preview path. Update the `piece` subparser `help`/`description` text in `_closet_parser` (it says "Ask the intended use temperature in Celsius") to mention named ranges. `_closet` must open the store and pass it to `run_piece_session(..., store=store)`.

Edit `src/mascot_wardrobe/interfaces/prompts.py`: add `ask_ranges(read, write, profile, *, allow_keep=False)` and `ask_range_name(read, write, ranges)` and `ask_number(read, write, valid_numbers)`. `ask_ranges` returns `(ranges: tuple[str, ...], temperature_c: float | None)` or `KEEP` on a blank answer during a revision; it uses the label `temperature> `, parses a single token with `float()` then `validate_temperature` and `profile.classify` (same errors and 3 attempts as `ask_temperature`), otherwise requires every comma-separated token to be a `Category` value; EOF is `EndOfInput`. `ask_range_name` (used by `inventory change`) accepts exactly one category name. Keep `ask_temperature` and its tests unchanged.

Edit `src/mascot_wardrobe/interfaces/piece.py` for the range question and the inventory registration (new keyword `run_piece_session(closet, profile, read, write, *, wardrobe=None, store=None)`, `None` meaning "do not register", so library tests stay hermetic). `_make_piece` and `_revise` switch from `ask_temperature` to `ask_ranges` (blank keeps in `_revise`); a changed first range changes the template, and every revision re-registers the garment in place through `replaces` (the item's sha256 read before `edit_item`). `_default_id` and `_ask_item_id` additionally treat an id as taken when `store` reports it is not `closet_item_id_free` (default id skips it; a custom taken id is re-asked with the collision message). `_summary`, `_show` and `_description` gain `ranges` and keep `temperature_c`, which may now be `None` (shown only when present). Numeric Celsius answers behave as before, so the existing `tests/test_piece.py` scenarios stay valid and are extended, not rewritten.

## Skill `mudar-peca` (thin orchestrator)

Add a generic template `examples/hermes-skill/mudar-peca/SKILL.md` with no private paths: it instructs the agent to (1) run `mascot-wardrobe inventory ranges --country <code>` and ask the user which named range, (2) run `mascot-wardrobe inventory list --range <range> --json` and show EVERY returned number, name and range list without filtering or ranking, (3) ask the user for the garment number, (4) run `mascot-wardrobe inventory set-default --range <range> --number <n>`, (5) report the stored mapping. It states that it must not choose for the user, must not publish, and must not touch any gateway. The implementation only adds and tests this template. It does not search for, create or modify any Hermes profile or skills directory, in this environment or any other: after the CLI is implemented and verified, the orchestrator installs the user-local skill into its own active default profile through its skill tooling. Constraint from `tests/test_docs.py::test_tracked_text_has_no_personal_identifiers`: it scans every tracked and untracked non-ignored text file (`git ls-files -co --exclude-standard`), including this plan, the template and `docs/inventory.md`. The exact rule (`FORBIDDEN` in that test) is case-sensitive for the Hermes patterns: a literal dot immediately followed by the lowercase word `hermes` at a word boundary, or the lowercase word `hermes` immediately followed by a slash, is rejected; the former mascot name is the only case-insensitive pattern. The same test rejects absolute user-home and root-home prefixes, the macOS user prefix, and IP addresses other than loopback. The template, docs and this plan must therefore never spell the Hermes profile directory or any path segment of those forms. The directory name `examples/hermes-skill/` itself is fine, because `hermes-skill/` is not the word followed by a slash.

## Existing user art (outside Git)

After the code is reviewed and the CLI is verified, the implementation imports the preserved six-category collection (already stored and hash-verified outside Git; its location and the real data home are supplied by the orchestrator at run time and are never written into tracked files) only through `inventory import-manifest <collection>/manifest.json` with an explicit `--data-dir`/`MASCOT_WARDROBE_HOME` for the real local inventory. Verify: six garments, stable numbers 1 to 6 in category order of the manifest, `ranges` equal to each outfit's own category, style/palette from the manifest request, sha256 of every copied file equal to the manifest, the import archive untouched (hash before and after), a second import reporting six `unchanged` and no new numbers, and no default mapping created. Do not commit the archive, the inventory data or any private path.

## Files to modify or add

- Add `src/mascot_wardrobe/domain/inventory.py`, `src/mascot_wardrobe/infrastructure/inventory_store.py`, `src/mascot_wardrobe/infrastructure/asset_formats.py`, `src/mascot_wardrobe/application/inventory.py`.
- Edit `src/mascot_wardrobe/application/__init__.py` (re-exports, cycle-safe as described above), `interfaces/cli.py`, `interfaces/prompts.py`, `interfaces/piece.py`, and `src/mascot_wardrobe/closet.py`. The `closet.py` change only adds read-only `mascot_sha256` / `mask_sha256` properties: no schema, render or record change.
- Add `tests/conftest.py` setting `MASCOT_WARDROBE_HOME` in `os.environ` to a per-session temporary directory, so no test can write to the real user data directory. This includes the subprocess CLI tests in `tests/test_cli.py` and `tests/test_cli_closet.py` (they pass `dict(os.environ, ...)`) and `tests/test_scripts.py` (it copies `os.environ`). `monkeypatch` is function-scoped, so set it in `pytest_configure` (or a session-scoped autouse fixture writing `os.environ` directly with `tmp_path_factory`), and restore it afterwards. The "default resolves outside the repository" test must clear that variable locally with `monkeypatch.delenv`.
- Edit `scripts/e2e-smoke.sh`: it runs the installed CLI's `generate` twice with no data-dir override, and the e2e run is outside pytest, so the conftest does not cover it. After this change that run would register the example "sunny" garments into the real user inventory (the same data home that holds the protected import archive). Export `MASCOT_WARDROBE_HOME="$run/home"` before the first CLI call, and add one smoke step: `inventory list` shows six garments after the two `generate` runs (idempotent registration). Keep the `--live-weather` constraint checked by `test_scanner_gates_with_hook_and_scripts_are_executable`.
- Add tests: `tests/test_inventory_domain.py`, `tests/test_inventory_store.py`, `tests/test_inventory_import.py`, `tests/test_inventory_cli.py`, `tests/test_inventory_selection.py`; extend `tests/test_piece.py` (existing Celsius-answer scenarios kept as they are; new named-range, collision-id and registration scenarios added), `tests/test_prompts.py`, `tests/test_docs.py` (skill template is generic), `tests/test_sdist.py` (template in `REQUIRED`), `tests/test_cli.py` / `tests/test_cli_closet.py` (automatic registration, unchanged existing output keys).
- Docs: new `docs/inventory.md`, new `docs/adr/0005-format-agnostic-inventory.md` (`0004` is already taken by `docs/adr/0004-weather-and-time-semantics.md`), optionally added to the list in `test_adrs_and_project_documents_exist`, edit `docs/architecture.md`, `docs/closet.md` (registration and range question), `docs/traceability.md`, `README.md`, `CHANGELOG.md`, and `docs/scheduler.md`/`docs/integration.md` only to state that scheduler examples stay opt-in and that `inventory select` changes no gateway.
- Add `examples/hermes-skill/mudar-peca/SKILL.md`. Edit `MANIFEST.in`: it currently has `recursive-include examples *.png *.example`, so a `.md` file under `examples/` would be silently left out of the sdist. Change it to `recursive-include examples *.png *.example *.md`, and add the template to `REQUIRED` in `tests/test_sdist.py`. The wheel stays runtime-only: `test_wheel_from_sdist_is_runtime_only` already rejects `examples/`.

## What stays unchanged

`wardrobe/composer.py`, `wardrobe/fit.py`, `wardrobe/styles.py` (fit, mask clipping, protected pixels, deterministic rendering), `closet.py` schema (`SCHEMA_VERSION = 1`, renderer config, `closet.json` layout), `manifest.json` format and its readers, `select`, `sandbox`, `demo`, `publish` and the channel adapters, `infrastructure/profiles.json`, and the scheduler examples (they stay clearly opt-in; nothing is installed or activated). Existing CLI invocations keep working; the only new side effect is that `generate` and the `closet` item commands also register into the inventory (opt-out `--no-inventory` for `generate`; library functions `prepare_wardrobe`, `WardrobeAgent` and `Closet` remain free of inventory side effects). The unrelated untracked file `plans/2026-10-21_style-parameters-fit.md` is not edited, staged or committed; never use `git add -A` or `git add .`.

## Edge cases

- Numbers: first garment is 1; after removing garment 3 the next new garment is still the next unused number; `retired` blocks any reuse even after a crash or a manual file edit (`verify` fails if a number is both live and retired, or if `next_number` is not greater than every used number).
- Defaults: mapping a range to an item whose `ranges` do not include it succeeds (hint only). Setting a default for an unknown range name or a missing/removed number fails and writes nothing. Removing a mapped or active item is refused with the range names that use it.
- `inventory change` shows the whole inventory even when no item lists the range; an empty inventory prints a clear message and exits 2 without writing.
- Selection: unknown mapping returns `no-default`, exit 3, and leaves `inventory.json` byte-identical. A temperature on a threshold belongs to the warmer category (existing `classify` rule). Out-of-range or non-finite temperatures keep the existing `validate_temperature` errors.
- Import: a manifest with a wrong hash, an extra or missing outfit, a duplicate category, an unsafe slug, a symlinked or out-of-directory file, an oversized manifest, or a non-PNG asset aborts with nothing registered. A partial failure mid-batch (injected) leaves `inventory.json` and previously registered assets byte-identical. Re-running the same import is a no-op. A manifest from an older template set is imported as-is (the images are already rendered) with its recorded renderer config in `origin`; it is not re-rendered.
- A closet item edited through `closet edit` or the `closet piece` revision updates its own garment in place (same number, version +1, via `replaces`); a closet item removed from its closet leaves the inventory garment alone. Two closets with the same mascot and mask and the same item id but different bytes never share a garment: the second is refused (`closet add`) or fails atomically (`inventory import-closet`).
- Format adapters: asking for an unregistered format (for example `gif`, `svg`, `lottie`) fails with the "not supported" message; the docs list only `png`.
- Data location: `--data-dir`, then env, then XDG, then `~/.local/share/mascot-wardrobe`; the inventory is never created inside the repository by the CLI or the tests (the conftest redirect plus a test that the default resolves outside the repository root).
- Concurrency: two simultaneous transactions serialize on the lock; the second sees the first's numbers.
- Disk errors or `KeyboardInterrupt` during a transaction leave the previous commit intact.

## Test scenarios (strict TDD: write each failing test, observe RED, implement, observe GREEN)

1. Domain: monotonic numbers, no reuse after removal and after reload, set/replace/remove rules, defaults independent of suitability, purity (importing `domain.inventory` does not import Pillow, `os`-level I/O helpers or `infrastructure`).
2. Store: first-use creation, atomic commit, backup before change, lock behaviour, schema too new refused, injected v0 -> v1 migration with backup, tamper detection of an asset hash, failed transaction leaves bytes identical, data-home resolution order.
3. Importers: manifest (six items, categories as ranges, provenance and hashes preserved, no defaults created, idempotent second run, tampered file aborts, injected mid-batch failure atomic), closet (idempotent, in-place update with version bump only through `replaces`, `import-closet` of unchanged items with changed metadata reports `updated`, label-only `closet edit` refreshes the name without a version bump, ranges from `closet add --range` survive a later `closet edit` without `--range`, other garments untouched), single asset (valid PNG, unsupported format, duplicate bytes).
4. Selection: boundary temperatures for each of the four built-in profiles using their real thresholds, mapped range returns the user's number and sets `active`, unmapped range returns `no-default` with exit code 3 and no file change, the user's mapping is honoured even when the item's suitability excludes the range, a closer or "better suited" item is never chosen.
5. CLI: `inventory list` shows every item with its number, `inventory change` with scripted input (range, full list shown including non-suitable items, number, persisted mapping), non-interactive refusal, `set-default` errors, `generate`/`closet add/edit/piece` register automatically and a registration failure keeps the generated output and exits 2, `--no-inventory`, every subprocess test runs against the temporary home, the `mudar-peca` alias dispatches like `change`, `inventory list --range R` works without `--country`.
6. `closet piece`: a bare `12` still means 12 degrees Celsius and the existing boundary and out-of-range scenarios still pass; named ranges (`cold`, `cold,mild`) are accepted, mixed number-and-name answers and unknown names are rejected after 3 attempts, the template equals the first range, suitability ranges are recorded, style and palette are asked afresh for every piece, a revision leaves other garments and hashes untouched and re-registers only its own garment, the "another piece?" loop works, the inventory number is shown, and a default or custom item id already registered for the same mascot and mask is skipped or re-asked.
   Collision regression (in `tests/test_inventory_import.py`): two different closet roots built from the same mascot and mask, each with an item of the same id but different garment bytes. Register the first (garment #1). Then (a) `closet add` of that id in the second root is refused before rendering with the collision error and the second closet is unchanged; (b) for a second closet that already holds the id (made before registration existed), `inventory import-closet` fails atomically with the collision error; (c) in both cases the first garment's record (number, version, origin, hashes), its asset files and `inventory.json` bytes are identical to before, and no backup or extra number is created. Also: `closet edit` in the first closet still updates garment #1 in place (version 2, same number) through `replaces`, and a `replaces` value that does not match the current hash is a collision error.
7. Docs/packaging: `test_docs` finds no private paths in the plan, docs, skill template or tests; the skill template calls only documented commands and contains no gateway or publish command; sdist/wheel tests still pass, and the sdist contains the skill template; `scripts/e2e-smoke.sh` sets `MASCOT_WARDROBE_HOME` inside its run directory before the first CLI call (assert it in `tests/test_docs.py` or `tests/test_scripts.py`).
8. Regression: the existing 331 tests keep passing; fit/protected-pixel/streetwear distinction tests are untouched.

## Verification

```sh
unset ANTHROPIC_API_KEY
.venv/bin/python -m pytest -q tests/test_inventory_domain.py tests/test_inventory_store.py tests/test_inventory_import.py tests/test_inventory_selection.py tests/test_inventory_cli.py tests/test_piece.py tests/test_prompts.py
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python art/generate_art.py --check
.venv/bin/python -m build
scripts/e2e-smoke.sh   # must use its own MASCOT_WARDROBE_HOME; check the real data home is unchanged afterwards
scripts/scan-secrets.sh
git status --porcelain   # only the intended files plus the preserved untracked plan
```

Lint and format run as `ruff check .` and `ruff format --check .` (per CONTRIBUTING.md and docs/verification.md); ruff only reads Python files, so the preserved plan file is not scanned or reformatted. After the gates pass: import the preserved collection through the importer with the checks listed above, inspect the full diff, and report explicitly whether any commit, push, PR or merge happened. Commit only scoped files by name; push (fast-forward only to `origin/feat/style-parameters-fit`, explicitly authorized by the user, and it also publishes `bab4ab1` and `8f47764`) only after every gate and review has passed, never earlier; no PR, no merge. The previous `small-change` run's final-review pass never reported, so the implementation must end with a final review that covers the whole branch diff from `daed14d`.

## Resolved decisions (given by the user; do not re-ask)

- Defaults are keyed by the range (category) name, not by country; the country only classifies a Celsius value and displays boundaries. The `mudar-peca` flow asks for a named range, then shows all inventory items, then takes the user's numeric garment number.
- Recovery from a migrated or failed-registration closet item uses the narrow `migrated_from.sha256` / explicit `--item ID --replaces SHA` paths described under the collision rule (a planning default consistent with the user's "explicit edit path" instruction).
- A missing mapping exits with code 3 (`no-default`) rather than 0 (plan default, not contradicted).
- Removing an item that is a default or the active garment is refused instead of silently clearing the mapping (plan default, not contradicted).
- No per-closet UUID: item ids are unique across closets that share a mascot sha256 and mask sha256; ambiguous duplicates fail atomically with a collision error; explicit edits go through `replaces`.
- All generated and closet items are registered automatically; `generate --no-inventory` is the only opt-out; all tests and e2e redirect data to temporary roots.
- `closet piece` keeps numeric Celsius input unchanged (a bare `12` is 12 degrees) and additionally accepts named ranges; there are no numeric range indices and no unit suffix.
- Scope changes to the e2e script, tests, docs, `MANIFEST.in` and the CLI/API are accepted where needed to satisfy the behaviour and protect real user data.
- The user-local skill is installed by the orchestrator through its skill tooling after verification; the implementation only adds the repository template.
- A fast-forward-only push to `origin/feat/style-parameters-fit` is authorized after all implementation, tests and reviews pass; no PR, no merge.

## Due diligence record

This section records what the plan due-diligence pass concluded about the external facts this plan cites or defers. Later passes, including the implementation-phase fact check, read it. It records what was concluded once, not what is true now: no pass may treat a line here as a reason to skip a verification. If a human edits one of these facts by hand, delete the matching line here rather than updating it, so this section never asserts something nobody checked.

- SETTLED: `$XDG_DATA_HOME/mascot-wardrobe`, used only when `XDG_DATA_HOME` is set, non-empty and absolute (a relative value is ignored), else `~/.local/share/mascot-wardrobe` (verified against the freedesktop XDG Base Directory Specification, latest)
- SETTLED: `add_parser("change", aliases=["mudar-peca"])` stores the typed name (`"mudar-peca"`) in the subparsers `dest`, so dispatch must accept both names (verified empirically on the project venv's Python 3.12.3 argparse; the Python docs page does not state it explicitly)
- SETTLED: `recursive-include examples *.png *.example *.md` includes files matching any listed pattern (verified against the setuptools MANIFEST.in command reference)
- SETTLED: `fcntl.flock` and `os.replace` exist on the target platform (verified on the project venv's Python 3.12.3, Linux)
- SETTLED: PNG adapter rules (`n_frames == 1`, `16_777_216` pixels, `Image.DecompressionBombError`) reuse the rules already in the codebase, so they carry no new Pillow API dependency (verified against the local source, no live lookup needed)
- SETTLED (by the user, not web-verified): the Hermes skill is installed by the orchestrator through its skill tooling after the CLI is verified; the implementation only adds the repository template and never touches a Hermes profile directory
- SETTLED (by the user): closet item identity collisions are handled by id uniqueness per mascot and mask hash, with no `closet.json` schema change


## Implementation steps

Execution constraints for this implementation override the earlier delivery paragraph: leave
all changes unstaged and uncommitted; no push, PR or merge. The external orchestrator performs
the real collection import and local skill installation after verification. This run uses only
temporary inventory roots and does not modify the preserved collection or any live target.

The continuation after the cutoff uses only the primary Codex thread. Implementer, hardener,
fact-check, standards and final-review passes run sequentially in that thread; no subagents,
delegation, agent worktrees or Claude runtime are used. Earlier parallel ownership notes below
remain historical contracts. The independent-agent review in step 10 is replaced by a separate
sequential review pass, and must not be reported as an independent review.

### Frozen contracts and ownership

- Domain values use the dataclasses above. `Inventory()` is empty v1; methods are immutable
  `add(**fields)`, `replace_asset(number, asset)`, `remove(number)`, `set_default(range, number)`,
  `default_for(range)`, `describe(number)`, `find_by_origin_key(key)` and
  `closet_item_id_free(mascot_sha256, mask_sha256, item_id)`. The origin key is a tuple:
  `("manifest", png_sha256)`, `("asset", source_sha256)`, or
  `("closet", mascot_sha256, mask_sha256, item)`. Shared domain helpers are
  `origin_key(origin)`, `inventory_to_dict(inventory)`, `inventory_from_dict(data)`.
  Replacement appends the previous portrait hash to `origin.history` and increments version.
- `InventoryStore.open(home)` exposes `root` (the inventory directory), `home`, `read()`,
  `transaction()`, `asset_path(asset_file)`, and `verify()`. A transaction exposes mutable
  `inventory` and `stage_asset(number, role, source_path, *, extension=None) -> AssetFile`.
  Staging hashes the copied snapshot. No-op transactions create no backup or metadata write.
  Fixed asset paths require a durable journal: recovery under the exclusive lock restores
  old files if JSON never reached the commit point, or finishes cleanup after it did.
  Exception rollback alone is insufficient. All commits and recovery include directory fsync.
- Application owns import identities, guards, metadata, range policies and selection. Shared
  functions retain the signatures above; additionally expose
  `closet_item_id_free(closet, item_id, store)` and
  `require_closet_item_id_free(closet, item_id, store)` for CLI and piece preflight checks.
  `register_closet(closet, store, *, item_id=None, replaces=None)` supports the narrow retry.
  Single registration results have `number` and `status`; batches have `imported` and `skipped`
  number lists, plus an optional `updated` number list when a closet batch changes existing
  garments. Selection accepts store, country, temperature, unit and optional profiles.
- Only the primary integrator edits CLI, application re-exports and shared test isolation.
  Independent owners edit domain/tests; store/adapters/tests; application inventory/tests;
  prompts/piece/their tests; or docs/package/e2e/their tests. No overlapping concurrent writes.
  Each owner records focused RED and GREEN evidence before reporting a slice complete.
- Protect tests with a session temporary data home, restored at shutdown, plus per-test homes
  inherited by subprocesses. This prevents unrelated fixtures with identical identity hashes
  from colliding while preserving the collision policy itself.

### Ordered, falsifiable slices

Each behavior follows a focused failing test, observed intended RED, smallest implementation,
targeted GREEN, then refactor. Dependencies below determine integration order; disjoint files
may progress concurrently after these contracts are frozen.

1. Establish test-home isolation before any automatic-registration test. Prove environment
   inheritance and data-home precedence; export the e2e home before its first CLI call.
2. Implement pure inventory allocation, validation, retirement, origin lookup, identity checks,
   asset replacement and defaults. Prove monotonic numbers and suitability-independent mapping.
3. Implement static PNG/companion adapters and transactional storage. Prove schema/migration,
   strict types, safe paths, hash checks, backups, process locking, no-op behavior, failure and
   crash recovery at asset installation and the JSON commit point.
4. Implement manual and six-category manifest import. Prove bounded/strict manifest validation,
   all role hashes, recorded provenance, idempotency, unsupported formats and full batch rollback.
5. Implement closet registration and the two public identity properties. Prove two-root
   collisions, metadata-only refresh, range retention, guarded replacement, migration recovery
   and single-item retry. Preserve closet schema and renderer behavior.
6. Implement complete ordered views, range boundaries, user defaults, removal and selection.
   Prove all profile thresholds, explicit unsuitable mappings, active updates and byte-identical
   no-default results with no backup or allocation.
7. Integrate inventory CLI, interactive named range/full numbered list/user number, alias,
   noninteractive guidance, argument errors and exit 3. Then integrate automatic generation and
   closet registration, pre-render identity checks, failure preservation and exact retry commands.
8. Extend piece prompts and revisions with named ranges while preserving every existing bare
   Celsius scenario. Prove separate per-piece choices, metadata range persistence, registered
   numbers, guarded revisions, id skipping/reasking, EOF and create-another behavior.
9. Add generic skill template, guide, ADR and traceability; update packaging and e2e assertions.
   Prove documented commands, no private paths or gateway commands, template in sdist and
   runtime-only wheel. Scheduler examples remain opt-in and inactive.
10. Run all plan gates, inspect the complete working diff and branch diff from the remote base,
    obtain an independent Codex review with test-gap/security checks, fix findings with TDD,
    and repeat the full suite and affected gates after late edits. Verify the unrelated plan
    and external collection are unchanged. Report remaining limitations without claiming a
    real import, publication, commit or push.

### Requirement-to-test mapping

| Requirement | Falsifiable coverage |
| --- | --- |
| Pure format-agnostic model; stable numbers; retirement | `test_inventory_domain.py`: purity, arbitrary format labels, validation, allocation, reload, replace/remove |
| Persistent external data root and protected test data | `test_inventory_store.py`: precedence/symlinks; isolation fixture and subprocess tests; e2e home assertion |
| Atomic writes, backups, migration, crash recovery and serialization | `test_inventory_store.py`: injected failures, killed process recovery, real concurrent processes, schema/type/hash tampering |
| Static PNG adapter and companions only | Store/import tests: unsupported formats, animation, dimensions, decode errors, role/extension constraints |
| Six-category manifest/manual import and idempotency | `test_inventory_import.py`: categories/order, provenance, all hashes, old manifests, malformed input and rollback |
| Closet identity, metadata refresh and narrow guarded recovery | `test_inventory_import.py`, `test_cli_closet.py`: two-root regression, preflight, stale guards, migration, range retention, retries |
| Suitability is descriptive; exact saved defaults only | Domain tests and `test_inventory_selection.py`: all profiles/boundaries, complete view, unsuitable mapping, no ranking |
| Missing mapping writes nothing and exits 3 | Selection/CLI tests: JSON/assets/backups/active/counters before and after |
| Named range, every garment, user-selected number | `test_inventory_cli.py`, `test_prompts.py`: scripted interaction, invalid/EOF answers, alias, noninteractive refusal |
| Default registration, explicit opt-out and failure preservation | Inventory/legacy CLI tests: generate/add/edit/piece, no library side effects, retry commands |
| Legacy Celsius and independent per-piece choices | Existing `test_piece.py` scenarios plus named ranges, revision, collision ids and repeated creation tests |
| Generic skill, docs, source archive and offline e2e | `test_docs.py`, `test_sdist.py`, `test_scripts.py`, installed-wheel smoke test |
| Existing fit, identity, mask and protected pixels | Unmodified compositor/fit/streetwear and legacy regression tests; full suite |
| Complete delivery audit | Lint, format, art check, build, e2e, secrets scan, independent full-diff review |

## Single-thread continuation audit

The continuation preserved the existing unstaged implementation at `8f47764`, including the
unrelated untracked plan. Steps 1–9 were already implemented; their domain, storage, adapter,
import, selection, CLI, piece, documentation and packaging slices were reviewed against the
contracts above. Prior focused RED/GREEN evidence was available outside the repository.
No renderer, profile or closet schema changes were needed in this continuation.

The sequential hardening pass found one additional invalid-answer case: a garment-number
answer exceeding Python's integer-conversion limit escaped the prompt instead of retrying.
`test_oversized_garment_number_reasks_without_selecting_a_default` first failed with that
`ValueError`; the prompt now catches the conversion failure and asks again, without selecting
or saving anything. Focused prompt and inventory CLI verification passed after the fix.

Step 10 includes a sequential review of the complete working changes, new files and branch
diff from `daed14d`, covering DDD boundaries, user-only mappings, collision guards, atomic
imports and journal recovery. This is a review in the primary thread, not an independent-agent
review. Ruff lint/format, generated-art verification, secret scanning, source/wheel packaging,
build and the installed-wheel offline e2e smoke passed. Isolated builds used an existing local
wheel cache because this environment cannot download dependencies; no dependency constraints
or tests were weakened.

The full test gate remains incomplete in this sandbox: legacy HTTP integration tests fail
when creating loopback sockets with `PermissionError: Operation not permitted`. These tests
were executed rather than skipped or mocked to manufacture a passing gate. The original full
run also encountered dependency-download errors in the packaging fixture; the packaging tests
passed when repeated with the local wheel cache. Full-suite results must be reported with
this limitation, and the suite still needs a run in an environment permitting loopback sockets.

The fact-check pass re-read the official
[XDG specification](https://specifications.freedesktop.org/basedir/latest/) and
[setuptools manifest reference](https://setuptools.pypa.io/en/latest/userguide/miscellaneous.html),
confirming the data-home fallback/absolute-path rules and multi-pattern recursive inclusion.
Argparse alias dispatch and the target platform's `flock`/`replace` availability were checked
empirically; PNG validation was compared with the existing compositor rules.

The external collection and real data home remain untouched; hashes and modification times
are compared with the continuation's initial snapshot. No real collection import, skill
installation, staging, commit, push, PR, merge, publication, profile/photo change or scheduler
activation is part of this run. Release readiness is not claimed while the full test gate
remains incomplete.

## Native parallel continuation review

The subsequent user instruction supersedes the earlier single-thread execution restriction.
Exactly eight native Codex agents started and completed read-only reviews before code edits:
domain invariants; storage atomicity/concurrency; import identities/migration; selection and
change flow; legacy CLI/prompts; asset formats/protected pixels; docs/packaging; independent
acceptance/security/test gaps. Changes remain serialized through the primary integrator.

The review led to focused RED/GREEN fixes for missing-metadata reinitialization, manifest
companion collisions, lost suitability ranges in printed recovery commands, and failed first
edit registration recovery. A follow-up domain audit also enforced the user's one-or-more
suitability-range invariant, including loaded records and atomic manual-import refusal.
Existing tests retain their assertions; fixtures unrelated to range validation now provide
an explicit valid range. Additional acceptance coverage checks missing defaults while another
garment is active and neighboring ranges have mappings.

Single-item closet recovery accepts repeatable `--range` arguments. The explicit
`--allow-first-registration` option requires `--item` and `--replaces`; it only permits
allocation when no identity exists. Existing garments still require an exact previous-hash
match, including when this option is present. Batch scope and collision guards remain intact.

Final gate evidence and the exact loopback-blocked test list are kept outside the repository
for the orchestrator. The real collection import, local checkpoint commit and later handoff
remain orchestrator responsibilities; this continuation performs none of them.
