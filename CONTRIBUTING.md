# Contributing

Thanks for helping. This project is small and aims to stay offline, deterministic and easy to review.

## Setup

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
```

## Checks to run before sending a change

```sh
python -m pytest -q
ruff check .
ruff format --check .
python -m build            # wheel and sdist; dist/ and build/ are git-ignored
scripts/e2e-smoke.sh       # fresh venv, installed wheel, offline flow
scripts/scan-secrets.sh    # detect-secrets against the tracked baseline
```

## Guidelines

- Test first. New or changed behavior comes with a test that failed before the change.
- Keep the layering described in `docs/architecture.md`: `domain` stays pure, adapters stay
  behind the application use cases.
- No personal data in tracked files: no machine paths, user names or real addresses
  (`tests/test_docs.py` scans for them). Use benign placeholders such as `example` or `sunny`.
- No real account changes, no credentials, no network in tests. Weather tests use the
  injectable `opener`.
- Artwork lives in `art/generate_art.py`; edit the generator and regenerate rather than
  editing the PNGs. `python art/generate_art.py --check` must pass.
- Update `CHANGELOG.md` and, when an acceptance criterion is affected, `docs/traceability.md`.

By contributing you agree your work is released under the MIT license.
