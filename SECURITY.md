# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use the hosting repository's private
vulnerability reporting feature if it is enabled; otherwise contact the maintainers privately.
This repository is currently developed locally and has no public remote, so no reporting
channel can be verified yet. Once the project is hosted, this section will name the exact
channel.

Include the affected version, steps to reproduce and the impact. Reports are handled on a best
effort basis; there is no service level agreement.

## Scope

In scope: path handling (traversal and symlink checks), input validation of PNGs, slugs and
manifests, the loopback sandbox bridge, the Open-Meteo client and the build and CI scripts.

## Not supported

- Real account changes. Channels are export-only file copies, and the sandbox is a local test
  fixture, not a hardened service. The sandbox is a logical boundary, not operating system
  isolation, and does not defend against a privileged local attacker.
- Processing untrusted images at scale. Input PNGs are size-bounded, but the project is not a
  hardened image pipeline.
- Third-party services. Open-Meteo availability and data are outside this project.

## Secrets

The code needs no credentials. Do not commit tokens, `.env` files or generated output.
`scripts/scan-secrets.sh` runs detect-secrets against the tracked baseline before commits.
