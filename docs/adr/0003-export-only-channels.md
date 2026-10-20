# ADR 0003: Export-only channels, amended for opt-in live adapters

Status: amended (originally accepted as export-only)

## Context

Delivering an outfit to a messaging account needs credentials, authorization of the target and
read-back verification, and an automated tool that changes real accounts is risky. The original
decision excluded real publishing entirely. The owner now wants one selected outfit presented as
a Hermes pet atlas plus a square profile JPEG for their own WhatsApp (local Hermes/Baileys
bridge) and for a Telegram bot, on a schedule.

## Decision

Sandbox channel is preserved unchanged. Channels (`whatsapp`, `telegram`, `discord`,
`hermes-cli`) copy files into `<export-dir>/<channel>/<slug>/` and report `remote_sync: false`.
The loopback `SandboxBridge` with `WhatsAppBridgeChannel` stays a contract test: loopback only,
neutral header `X-Mascot-Wardrobe-Sandbox`, port 3000 refused, `sandbox: true`.

Amended by this ADR: live adapters (`mascot_wardrobe.channels.live`) are added behind a new
`publish` command with these safeguards:

- **Dry-run is the default.** Only `--apply` changes anything, and applying with no configured
  channel is refused. `generate` never publishes.
- **Narrow scope.** WhatsApp only through the Hermes bridge on loopback (the adapter runs on the
  Hermes host; non-loopback destinations are refused), updating the connected account itself
  with a slug only. Telegram only for the bot's own profile photo via `setMyProfilePhoto`.
- **Authorization.** A bridge directory and URL, and for Telegram a bot id, must be configured
  explicitly; there are no defaults pointing at live resources.
- **Secrets.** Tokens come from the environment or an injected provider, are never stored,
  logged or printed, and request URLs are never shown.
- **Honest verification.** WhatsApp is `verified` only after a matching readback of
  `target: self`; Telegram is reported as `acknowledged`, `verified: false`.
- **Bounds.** Timeouts, bounded responses, redirects and proxies denied, atomic state writes,
  idempotent repeats, redacted failures.
- **No inference.** No LLM, image API or paid service: the wardrobe stays local Pillow.
  Weather remains the opt-in free Open-Meteo request.

## Consequences

- Safe by default and simple to test offline; live paths are covered with fakes only.
- A real run can change a real account photo; rollback is manual (see `docs/channels.md`).
- The WhatsApp readback field names are an assumption modeled on the sandbox contract and must
  be checked against the real bridge by its owner.
- Scheduling stays an uninstalled example (`examples/publish/`).
