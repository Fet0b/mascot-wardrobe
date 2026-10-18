# Channels

Channels are export-only. Nothing in this project changes a real WhatsApp, Telegram, Discord
or other account, and no credentials are required or read.

Two kinds of destination exist:

- **File export** (`FileExportChannel`): copies files into a folder. Used for `telegram`,
  `discord`, `hermes-cli` and, as a library class, for `whatsapp`.
- **Loopback sandbox bridge** (`SandboxBridge` plus `WhatsAppBridgeChannel`): a local HTTP
  server on `127.0.0.1` that exercises an update-and-read-back contract against files. It is a
  local contract test, not a WhatsApp integration.

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

Changing the avatar of a Telegram bot is not available in this project. The project never calls
any Telegram API and never claims to; that follows from the export-only decision (ADR 0003), not
from a statement about what any platform offers. The same holds for every other service. A
real integration would need explicit target authorization, credentials kept outside the
repository, a provider adapter and a read-back check, and is out of scope.

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

The sandbox is a logical boundary in the application, not operating system isolation. A
privileged process could swap directories or impersonate the bridge, and the `sandbox: true`
flag does not authenticate a hostile server. Run it in dedicated folders without competing
writers.
