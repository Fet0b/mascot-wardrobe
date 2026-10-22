#!/usr/bin/env python3
"""Read-only code/docs/config mirror comparison. Prints relative paths, never file contents."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

# Explicit exclusions, not .gitignore rules or extension-wide image/archive exclusions.
ROOT_EXCLUSIONS = frozenset(
    {
        ".git",
        ".venv",
        ".agents",
        ".codex",
        ".aws",
        ".claude",
        "build",
        "dist",
        "audit",
        "sandbox",
        "state",
        "logs",
        "output",
        "mascot-wardrobe-out",
        "data",
        "collections",
        "closets",
        "inventory",
        "catalog",
        "catalogs",
        "pets",
        "exports",
        "archives",
        "snapshots",
        "sessions",
        "credentials",
        "auth",
        "node_modules",
    }
)
CACHE_DIRECTORIES = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"})
PRIVATE_ROOT_FILES = frozenset({"config.yaml", "auth.json", "weather-publisher.json"})
GENERATED_SUFFIXES = (".pyc", ".pyo", ".log", ".egg-info")


def _excluded(relative):
    return (
        relative.parts[0] in ROOT_EXCLUSIONS
        or bool(CACHE_DIRECTORIES.intersection(relative.parts))
        or len(relative.parts) == 1
        and relative.name in PRIVATE_ROOT_FILES
        or relative.name == ".env"
        or relative.name.startswith(".env.")
        and relative.name != ".env.example"
        or relative.name.endswith(GENERATED_SUFFIXES)
    )


def manifest(root):
    raw = Path(root).expanduser().absolute()
    if any(path.is_symlink() for path in (raw, *raw.parents)) or not raw.is_dir():
        raise ValueError("Both roots must be existing real directories without symlinks")
    root = raw.resolve()
    result = {}

    def unreadable(error):
        raise error

    for directory, children, files in os.walk(root, followlinks=False, onerror=unreadable):
        for name in children[:]:
            path = Path(directory) / name
            relative = path.relative_to(root)
            if _excluded(relative):
                children.remove(name)
            elif path.is_symlink():
                raise ValueError(f"Included symlink is not allowed: {relative.as_posix()}")
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(root)
            if _excluded(relative):
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Included symlink/nonregular file: {relative.as_posix()}")
            with path.open("rb") as stream:
                result[relative.as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def compare(source, copy):
    source, copy = manifest(source), manifest(copy)
    missing = sorted(source.keys() - copy.keys())
    extra = sorted(copy.keys() - source.keys())
    changed = sorted(name for name in source.keys() & copy.keys() if source[name] != copy[name])
    return {"ok": not (missing or extra or changed), "missing": missing, "extra": extra, "changed": changed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--copy", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = compare(args.source, args.copy)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
