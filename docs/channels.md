# Channels

The sandbox channels are export-only. `generate`, `select`, `sandbox` and `demo` never change a
real WhatsApp, Telegram, Discord or other account and need no credentials. Real profile changes
exist only in the separate, explicitly opt-in `publish` command (see
[Live adapters](#live-adapters-opt-in)), which is a dry-run unless you pass `--apply`.

Three kinds of destination exist:

- **File export** (`FileExportChannel`): copies files into a folder. Used for `telegram`,
  `discord`, `hermes-cli` and, as a library class, for `whatsapp`.
- **Loopback sandbox bridge** (`SandboxBridge` plus `WhatsAppBridgeChannel`): a local HTTP
  server on `127.0.0.1` that exercises an update-and-read-back contract against files. It is a
  local contract test, not a WhatsApp integration.
- **Live adapters** (`mascot_wardrobe.channels.live`): the Hermes WhatsApp bridge client and the
  Telegram bot client. They never masquerade as sandbox channels (`sandbox: false`).

## CLI routing

`mascot-wardrobe sandbox|demo --channel <name>`:

| Channel | What happens |
| --- | --- |
| `whatsapp` (default) | `sync_sandbox` starts the loopback bridge, copies the selected JPEG to `<state-dir>/current.jpg` and reads it back. Reports `updated` then `unchanged`. |
| `telegram`, `discord`, `hermes-cli` | `export_outfit` copies files with `FileExportChannel`. Reports `exported` then `unchanged`. |

File export for the `whatsapp` channel name is available through `FileExportChannel` (and is
covered by its unit test); the CLI keeps routing `--channel whatsapp` to the bridge.

## Sandbox export versus real account changes

A sandbox export or a bridge run proves that bytes were copied to a local folder or file. It
does not prove that anything was published, and a real service may recompress images.

Changing a real account through the sandbox or file-export channels is not available in this project:
they only copy files and talk to a local fixture. Real changes need the live adapters
below and a deliberate `--apply` (ADR 0003, amended).

## Live adapters (opt-in)

Scope is deliberately narrow: **WhatsApp through the local Hermes/Baileys bridge** and **a
Telegram bot's own profile photo**. Nothing else (Discord, other bridges, personal Telegram
accounts) is supported.

```sh
# Dry-run (default): selects the outfit, validates the JPEG and the settings, prints the plan
# plus the atlas path for the Hermes pet. No network, no writes.
mascot-wardrobe publish --manifest mascot-wardrobe-out/catalog/manifest.json --country BR \
  --temperature 22 --whatsapp-catalog-dir /path/to/bridge/approved-photos \
  --whatsapp-bridge-url http://127.0.0.1:3000 --whatsapp-bridge-header "Name: value" \
  --telegram --telegram-expected-bot-id 123456789

# The same command with --apply really changes the profile photos.
```

Selection happens once; the same `<slug>.jpg` (512x512 square JPEG) goes to every configured
channel, and the result lists `atlas` (the `<slug>-atlas.png` for the Hermes pet) so all outputs
show the same outfit. With `--apply` and no configured channel the command refuses (exit 2).
Exit status is 0 when every configured channel succeeded, 1 when one failed (the other is still
attempted and reported), 2 for invalid configuration.

### WhatsApp via the Hermes bridge

- The bridge listens on `127.0.0.1` only, so this adapter must run on the Hermes host, or against
  a loopback endpoint you opened yourself. It refuses any other destination: the URL must be
  plain HTTP, a literal loopback IP, with an explicit port, no credentials, path, query or
  fragment. Proxies and redirects are disabled. It never calls `/health`.
- The bridge only reads prebuilt `<slug>.jpg` files from its own approved photo directory. Point
  `--whatsapp-catalog-dir` at that directory explicitly; there is no default and the project
  never points at a live directory by itself. The slug is validated, symbolic links are
  refused, and the file is copied atomically (identical bytes are not rewritten).
- The request carries only `{"slug": ...}`. No JID, path, URL or image bytes are sent, and no
  `Origin` header. The header your bridge requires is passed as `--whatsapp-bridge-header
  "Name: value"` or the `MASCOT_WARDROBE_WHATSAPP_BRIDGE_HEADER` environment variable; it is a
  contract value of your bridge, not stored in this repository.
- Flow: `GET /profile-picture?slug=...` readback; if it already matches, report `unchanged`;
  otherwise `POST`, require `success` and `target: self`, then read back again. `verified: true`
  is reported only when the follow-up readback says `target: self` and `matches: true` with a
  consistent image and hash. An acknowledgement without a matching readback is an error.
- Limit: the readback field names (`target`, `slug`, `matches`, `imageBase64`, `remoteSha256`) mirror
  the sandbox contract. They are tested only against fakes; check them against your bridge
  before relying on `--apply`.

### Telegram bot

- Uses the free official Bot API method `setMyProfilePhoto` with a static `.jpg`
  (`InputProfilePhotoStatic`, multipart `attach://`). It changes the profile photo of **the bot itself**,
  never the photo of a personal Telegram account, and needs a Bot API version that
  provides the method.
- Credentials: only from the environment (`MASCOT_WARDROBE_TELEGRAM_BOT_TOKEN` by default, or
  the name given by `--telegram-token-env`). The token is never stored, logged or printed, and
  the request URL (which contains it) is never shown; failures report only the method and the
  HTTP status.
- Identity: `--telegram-expected-bot-id` is required; `getMe` must report a bot with that id
  before any photo changes.
- Verification: no reliable readback is used, so the result is `acknowledged: true`,
  `verified: false`, `verification: "unverified-ack-only"`. Telegram is never reported as verified. The acknowledgement must be exactly `true`.
- Idempotency: the SHA-256 of the last acknowledged JPEG is kept in
  `<telegram-state-dir>/telegram-profile.json` (atomic write, symbolic links refused); the same
  image is not sent again unless `--force`.
- Limit: not exercised against the real service in this repository; tests use a fake transport
  and a loopback fake server. Timeout 10 s, responses bounded to 1 MiB, redirects denied.

### Disable and rollback

Live behavior needs `--apply` and a configured channel, so removing `--apply` or the channel
flags (or the scheduler entry, see [scheduler](scheduler.md)) stops it. There is no automatic
undo: to roll back, run `publish --apply` again with a different temperature or restore the old
photo through the same bridge or through BotFather. Delete the bot token from your environment
when you stop using the adapter.

## File exports

```python
from pathlib import Path
from mascot_wardrobe.channels import FileExportChannel

channel = FileExportChannel(Path("exports"), "telegram")
result = channel.export("sunny-br-warm", Path("a.jpg"), Path("a-atlas.png"), Path("manifest.json"))
```

- `FileExportChannel(sandbox_dir, channel)` where `channel` is `whatsapp`, `telegram`,
  `discord` or `hermes-cli`; anything else is a `ValueError`.
- `.export(slug, portrait, atlas, manifest)` copies bytes unchanged to
  `<sandbox_dir>/<channel>/<slug>/<original-name>`. The three file names must be distinct.
- Inputs and destinations are validated before writing; symbolic links and files outside the
  declared root are refused. Each file is written atomically, but the set is not transactional.
- The result has `slug`, `channel`, `sandbox: true`, `action` (`exported` or `unchanged`),
  `remote_sync: false`, `verified: false` and `files` (portrait, atlas, manifest). A second run
  with identical bytes reports `unchanged` and does not rewrite files, so mtimes and hashes are
  stable.

## Loopback sandbox bridge

```python
from pathlib import Path
from mascot_wardrobe.channels import SandboxBridge, WhatsAppBridgeChannel

with SandboxBridge(Path("catalog"), Path("state")) as bridge:
    result = WhatsAppBridgeChannel(bridge.base_url, timeout=5).sync("sunny-br-warm")
```

- `SandboxBridge(catalog_dir, state_dir)` is a context manager exposing `.base_url` and `.port`.
  It uses `ThreadingHTTPServer`, binds to `127.0.0.1` only on an ephemeral port and stops the
  server and thread on exit.
- The catalog holds JPEGs named `<slug>.jpg`, at most 2 MiB, checked with Pillow. Slugs match
  `[a-z0-9][a-z0-9-]{0,100}`. The catalog and state directories must not overlap.
- State is `state_dir/current.jpg`, written atomically; identical content is not rewritten.
- `GET /health` returns `{"status":"connected","sandbox":true}`.
- `GET /profile-picture?slug=...` and `POST /profile-picture` require the header
  `X-Mascot-Wardrobe-Sandbox: 1`, a local contract-test header and not part of any real API.
  Any `Origin` header is rejected. POST accepts only `{"slug": "..."}`; targets, paths,
  traversal and oversized bodies are rejected.
- The client checks health, reads the current state, posts only when needed and then
  reads back again; a missing read-back confirmation is a `RuntimeError`. There are no hidden
  retries.
- The base URL must be HTTP with a literal loopback IP and an explicit port other than 3000.
  `localhost`, credentials, queries, fragments and base paths are refused. Proxies and
  redirects are disabled.
- The client returns `action` (`updated` or `unchanged`), `verified: true`, `sandbox: true`
  and the SHA-256 of the read-back bytes. This verifies the simulated copy only.

## Security limits

The live adapters add real network paths: the Telegram client may reach only
`https://api.telegram.org`, and the bridge client only a loopback address. Tests never contact
either; they use fake credentials, fake transports and fake loopback servers.

The sandbox is a logical boundary in the application, not operating system isolation. A
privileged process could swap directories or impersonate the bridge, and the `sandbox: true`
flag does not authenticate a hostile server. Run it in dedicated folders without competing
writers.
