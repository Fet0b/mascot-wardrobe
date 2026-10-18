# ADR 0003: Export-only channels

Status: accepted

## Context

Delivering an outfit to a messaging account needs credentials, authorization of the target and
read-back verification, and an automated tool that changes real accounts is risky.

## Decision

Channels (`whatsapp`, `telegram`, `discord`, `hermes-cli`) copy files into
`<export-dir>/<channel>/<slug>/` and report `remote_sync: false`. Repeats with identical bytes
report `unchanged`.

A loopback HTTP sandbox bridge (`SandboxBridge` with `WhatsAppBridgeChannel`) is retained as
a contract test of an update and read-back flow. It binds `127.0.0.1` only, uses the neutral
header `X-Mascot-Wardrobe-Sandbox` and is documented as local, not a WhatsApp integration. The
CLI keeps routing `--channel whatsapp` to this bridge; file export for that channel name is a
library path covered by the `FileExportChannel` tests.

No real account is changed and no credentials are read. Changing a Telegram bot's avatar is
not available in this project: the project does not call that, or any other, platform API. This
is a scope decision, not a statement about what any platform allows.

## Consequences

- Safe by default and simple to test offline.
- Real publishing remains future work needing explicit authorization, secrets handling outside
  the repository, a provider adapter and a read-back check.
