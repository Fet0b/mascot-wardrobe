# Installation and integration guide

This guide takes you from a clean checkout to a verified, opt-in `publish` run. Every command
below exists in the CLI; nothing is installed as a service by this project.

## What is and is not wired

- `inventory select` resolves only the user's saved range default and changes no gateway,
  profile or photo. The generic inventory skill is a separate orchestrator template; installing
  it or connecting its results to publication is outside the inventory commands. See
  [inventory](inventory.md).
- `mascot-wardrobe publish` is Wardrobe's own opt-in publisher. It selects one outfit from the
  manifest and, only with `--apply`, delivers the same JPEG to the channels you configure
  (see [channels](channels.md)).
- A Hermes weather scheduler (an updater or timer you installed separately) is **not** part of
  this repository and is **not** wired to it. Installing Wardrobe does not change that
  scheduler, its timers or its WhatsApp bridge, and nothing here discovers or modifies them.
- To make a scheduler call Wardrobe you must run the same `publish` command from it yourself.
  The only templates are the uninstalled `.example` files in `examples/publish/` (see
  [scheduler](scheduler.md)). Whether the Hermes pet reads the atlas that `publish` prints is
  your own configuration; this repository does not do it.

## 1. Clean install

Python 3.11 or newer is required. In a clean virtual environment, from the checkout:

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'      # use `pip install .` if you only need the command
mascot-wardrobe --help
```

## 2. Verify the install

```sh
python -m pytest -q
ruff check .
ruff format --check .
python -m build              # needs the `build` module, part of the dev extra
scripts/e2e-smoke.sh         # fresh venv, installed wheel, offline flow
```

None of these contact a real account or need credentials. See [verification](verification.md).

## 3. Generate the wardrobe once

```sh
mascot-wardrobe generate --mascot examples/mascot.png --id sunny --country BR \
  --body-box 0.125 0.555 0.875 0.915 --style classic --palette auto
```

This writes `mascot-wardrobe-out/catalog/manifest.json` and the six outfits locally. Use your
own mascot and body box for real use (see [wardrobe](wardrobe.md)). `--style` and `--palette` are required when not
interactive.

## 4. Dry-run first

`publish` is a dry-run unless `--apply` is given. The dry-run selects the outfit, validates the
JPEG and the channel settings, and prints `slug`, `jpg`, `atlas`, `jpg_sha256` and each channel
as `planned`. It does not contact the bridge or Telegram and does not write files.

```sh
mascot-wardrobe publish --manifest mascot-wardrobe-out/catalog/manifest.json --country BR \
  --temperature 22 \
  --whatsapp-catalog-dir /path/to/bridge/approved-photos \
  --whatsapp-bridge-url http://127.0.0.1:3000 --whatsapp-bridge-header "Name: value" \
  --telegram --telegram-expected-bot-id 123456789
```

With `--temperature` there is no network at all. `--live-weather` (with `--latitude` and
`--longitude`) additionally calls Open-Meteo, even in a dry-run. Exit status: 0 all channels
succeeded, 1 a channel failed, 2 invalid configuration (including `--apply` with no channel).

## 5. Channel prerequisites

### WhatsApp (Hermes/Baileys bridge)

- Run `publish` on the machine that hosts the bridge: the URL must be plain HTTP on a literal
  loopback IP with an explicit port.
- `--whatsapp-catalog-dir` must be an existing real directory that your bridge reads approved
  `<slug>.jpg` files from. There is no default and symbolic links are refused.
- The state directory defaults to `mascot-wardrobe-out/whatsapp-state` and can be set with
  `--whatsapp-state-dir`. It keeps the lock, previous-photo backup and sync record outside
  the approved photo catalog; choose a directory with appropriate local permissions.
- Give the header your bridge requires with `--whatsapp-bridge-header "Name: value"` or the
  `MASCOT_WARDROBE_WHATSAPP_BRIDGE_HEADER` environment variable. These settings are separate
  from the catalog and state directories.
- The readback field names are tested only against fakes. Compare them with your bridge before
  the first `--apply` (see [channels](channels.md)).

### Telegram (the bot itself)

- Only a bot's own profile photo is changed, never a personal account.
- Export the token in the environment, for example
  `MASCOT_WARDROBE_TELEGRAM_BOT_TOKEN` (or name another variable with `--telegram-token-env`).
  Do not put it on the command line or in a tracked file.
- `--telegram-expected-bot-id` is required; `getMe` must match it before any photo changes.
- Success means acknowledged, never verified. The last acknowledged image hash is kept in
  `<telegram-state-dir>/telegram-profile.json` (default `mascot-wardrobe-out/state`); use
  `--force` to resend an unchanged image.

## 6. Explicit live publication

Only after a clean dry-run and the checks above, repeat the same command with `--apply`. Review
the JSON: WhatsApp reports `verified: true` only on a matching bridge readback; Telegram reports
`acknowledged: true` and `verified: false`.

## 7. Scheduling (optional, yours to install)

Copy and adapt the files in `examples/publish/` yourself, keep tokens in an `EnvironmentFile`
outside the repository, and test the exact command without `--apply` first. A cron line or any
other scheduler works the same way: it only needs to run the same `publish` command.

## 8. Rollback

- Stop: remove `--apply` or the channel flags, or disable your scheduler entry, for example
  `systemctl --user disable --now mascot-wardrobe-publish.timer`, then delete the copied unit
  files.
- Revert a photo: there is no automatic undo. Run `publish --apply` with another temperature, or
  restore the old photo through the bridge or BotFather.
- Remove the bot token from your environment file when you stop using the adapter.
- Uninstall the command with `pip uninstall mascot-wardrobe` in the same virtual environment.
