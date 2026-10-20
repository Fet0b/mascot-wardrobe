# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem. Private vulnerability reporting is
enabled for this repository. Submit a confidential report at:

https://github.com/Fet0b/mascot-wardrobe/security/advisories/new

The repository's Security tab also provides the "Report a vulnerability" action.

Include the affected version, steps to reproduce and the impact. Reports are handled on a best
effort basis; there is no service level agreement.

## Scope

In scope: path handling (traversal and symlink checks), input validation of PNGs, slugs and
manifests, the loopback sandbox bridge, the live adapters (Hermes bridge client, Telegram bot client, `publish`), the Open-Meteo client and the build and CI scripts.

## Not supported

- Real account changes outside the documented live adapters (Hermes WhatsApp bridge on loopback,
  Telegram bot profile). Sandbox channels are export-only file copies, and the sandbox is a local test
  fixture, not a hardened service. The sandbox is a logical boundary, not operating system
  isolation, and does not defend against a privileged local attacker.
- Processing untrusted images at scale. Input PNGs are size-bounded, but the project is not a
  hardened image pipeline.
- Third-party services. Open-Meteo availability and data are outside this project.

## Secrets

Sandbox commands and dry-runs need no credentials. Live Telegram publishing requires a bot token from an environment variable; it is never stored or printed. WhatsApp publishing requires the configured local Hermes bridge header. Do not commit tokens, `.env` files or generated output.
`scripts/scan-secrets.sh` runs detect-secrets against the tracked baseline before commits.
