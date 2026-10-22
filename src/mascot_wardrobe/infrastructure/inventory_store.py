"""Locked, journaled persistence for the format-independent inventory."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1
MIGRATIONS = {}


def _safe_path(path):
    raw = Path(path).expanduser().absolute()
    if ".." in raw.parts or any(p.is_symlink() for p in (raw, *raw.parents)):
        raise ValueError("Inventory paths cannot contain traversal or symbolic links")
    return raw.resolve()


def data_home(override=None):
    """Resolve the data home lazily, following explicit, environment and XDG precedence."""
    if override is not None:
        return _safe_path(override)
    configured = os.environ.get("MASCOT_WARDROBE_HOME")
    if configured:
        return _safe_path(configured)
    xdg = os.environ.get("XDG_DATA_HOME", "")
    if xdg and Path(xdg).is_absolute():
        return _safe_path(Path(xdg) / "mascot-wardrobe")
    return _safe_path(Path.home() / ".local/share/mascot-wardrobe")


def _fsync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _make_directory(path):
    """Persist new directory entries as well as the files later placed in them."""
    path = _safe_path(path)
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    path.mkdir(parents=True, exist_ok=True)
    for created in missing:
        _fsync_dir(created)
        _fsync_dir(created.parent)


def _write_atomic(path, data):
    path = _safe_path(path)
    _make_directory(path.parent)
    descriptor, name = tempfile.mkstemp(prefix=".inventory-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _json_bytes(data):
    return (json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


class InventoryStore:
    """One inventory, serialized by a process lock and committed by its JSON pointer."""

    def __init__(self, home):
        self.home = _safe_path(home)
        self.root = _safe_path(self.home / "inventory")

    @classmethod
    def open(cls, home):
        store = cls(home)
        with store._locked():
            from mascot_wardrobe.domain.inventory import Inventory, inventory_to_dict

            if not (store.root / "inventory.json").exists():
                if (
                    any((store.root / "assets").iterdir())
                    or any((store.root / "backups").iterdir())
                    or any(
                        p.name not in ("assets", "backups", "inventory.lock") for p in store.root.iterdir()
                    )
                ):
                    raise ValueError("Inventory metadata is missing; preserve existing files for recovery")
                _write_atomic(store.root / "inventory.json", _json_bytes(inventory_to_dict(Inventory())))
            store._load()
        return store

    @contextmanager
    def _locked(self):
        _safe_path(self.root)
        _make_directory(self.root)
        for name in ("assets", "backups"):
            _make_directory(self.root / name)
        lock = _safe_path(self.root / "inventory.lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self._recover()
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _load(self):
        from mascot_wardrobe.domain.inventory import inventory_from_dict

        path = _safe_path(self.root / "inventory.json")
        raw = path.read_bytes()
        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Inventory is not valid JSON") from exc
        if not isinstance(data, dict) or type(data.get("schema_version")) is not int:
            raise ValueError("Inventory schema version must be an integer")
        version = data["schema_version"]
        if version > SCHEMA_VERSION or version < 0:
            raise ValueError(f"Unsupported inventory schema version {version}")
        migrated = version != SCHEMA_VERSION
        while version < SCHEMA_VERSION:
            migration = MIGRATIONS.get(version)
            if migration is None:
                raise ValueError(f"No migration for inventory schema version {version}")
            data = migration(data)
            if (
                not isinstance(data, dict)
                or type(data.get("schema_version")) is not int
                or data["schema_version"] != version + 1
            ):
                raise ValueError("Inventory migration must advance exactly one schema version")
            version += 1
        inventory = inventory_from_dict(data)
        self._validate_assets(inventory)
        if migrated:
            self._commit(_Transaction(self, inventory), raw)
            raw = path.read_bytes()
        return inventory, raw

    def _relative(self, relative):
        if not isinstance(relative, str):
            raise ValueError("Inventory asset path must be relative")
        parts = Path(relative).parts
        if len(parts) != 3 or parts[0] != "assets" or re.fullmatch(r"[1-9][0-9]*", parts[1]) is None:
            raise ValueError("Inventory asset path must be assets/<number>/<role>.<extension>")
        if re.fullmatch(r"[a-z][a-z0-9_-]{0,39}\.[a-z0-9]{1,16}", parts[2]) is None:
            raise ValueError("Unsafe inventory asset filename")
        return _safe_path(self.root / relative)

    def asset_path(self, asset_file):
        return self._relative(asset_file.path)

    def _validate_assets(self, inventory, staged=None):
        staged = {} if staged is None else staged
        seen = set()
        for garment in inventory.garments:
            for asset in garment.asset.files:
                path = self.asset_path(asset)
                if Path(asset.path).parts[1] != str(garment.number) or path.stem != asset.role:
                    raise ValueError("Inventory asset path must match its garment number and role")
                if asset.path in seen:
                    raise ValueError("Inventory assets must have distinct paths")
                seen.add(asset.path)
                actual = staged.get(asset.path, path)
                if not actual.is_file():
                    raise ValueError(f"Missing inventory asset: {asset.path}")
                if hashlib.sha256(actual.read_bytes()).hexdigest() != asset.sha256:
                    raise ValueError(f"Inventory asset sha256 mismatch: {asset.path}")

    def read(self):
        with self._locked():
            return self._load()[0]

    @contextmanager
    def transaction(self):
        with self._locked():
            inventory, original = self._load()
            transaction = _Transaction(self, inventory)
            try:
                yield transaction
                from mascot_wardrobe.domain.inventory import inventory_to_dict

                if inventory_to_dict(transaction.inventory) != inventory_to_dict(inventory):
                    self._commit(transaction, original)
            finally:
                transaction.cleanup()

    def _next_backup(self):
        number = 1
        while (self.root / "backups" / f"inventory-{number}.json").exists():
            number += 1
        return self.root / "backups" / f"inventory-{number}.json"

    def _install_asset(self, staged, target):
        _safe_path(target)
        _make_directory(target.parent)
        os.replace(staged, target)
        _fsync_dir(target.parent)

    def _commit(self, transaction, original):
        from mascot_wardrobe.domain.inventory import inventory_from_dict, inventory_to_dict

        data = inventory_to_dict(transaction.inventory)
        inventory_from_dict(data)
        self._validate_assets(transaction.inventory, transaction.staged)
        replacement = _json_bytes(data)
        transaction.ensure_directory()
        directory = transaction.directory
        entries = []
        used = {f.path for g in transaction.inventory.garments for f in g.asset.files}
        for index, (relative, staged) in enumerate(transaction.staged.items()):
            if relative not in used:
                continue
            target = self._relative(relative)
            previous = None
            if target.exists():
                previous = f"old-{index}"
                _write_atomic(directory / previous, target.read_bytes())
            entries.append({"target": relative, "previous": previous, "staged": staged.name})
        backup = self._next_backup()
        journal = {
            "schema_version": 1,
            "directory": directory.name,
            "before": hashlib.sha256(original).hexdigest(),
            "after": hashlib.sha256(replacement).hexdigest(),
            "backup": str(backup.relative_to(self.root)),
            "entries": entries,
        }
        _write_atomic(self.root / ".journal.json", _json_bytes(journal))
        try:
            with _safe_path(backup).open("xb") as stream:
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
            _fsync_dir(backup.parent)
            for entry in entries:
                self._install_asset(directory / entry["staged"], self._relative(entry["target"]))
            _write_atomic(self.root / "inventory.json", replacement)
        except BaseException:
            self._recover()
            raise
        self._recover()

    def _recover(self):
        journal_path = _safe_path(self.root / ".journal.json")
        if not journal_path.exists():
            return
        try:
            journal = json.loads(journal_path.read_bytes())
            directory_name = journal["directory"]
            if (
                not isinstance(directory_name, str)
                or re.fullmatch(r"\.transaction-[a-zA-Z0-9_-]+", directory_name) is None
            ):
                raise ValueError("Unsafe inventory recovery directory")
            directory = _safe_path(self.root / directory_name)
            backup_name = journal["backup"]
            if (
                not isinstance(backup_name, str)
                or re.fullmatch(r"backups/inventory-[1-9][0-9]*\.json", backup_name) is None
            ):
                raise ValueError("Unsafe inventory recovery backup")
            backup = _safe_path(self.root / backup_name)
            if journal["schema_version"] != 1 or not isinstance(journal["entries"], list):
                raise ValueError("Unsupported inventory recovery journal")
            for key in ("before", "after"):
                if not isinstance(journal[key], str) or re.fullmatch(r"[0-9a-f]{64}", journal[key]) is None:
                    raise ValueError("Invalid inventory recovery hash")
            entries = []
            for entry in journal["entries"]:
                target = self._relative(entry["target"])
                old = entry["previous"]
                if old is not None and (not isinstance(old, str) or re.fullmatch(r"old-[0-9]+", old) is None):
                    raise ValueError("Unsafe inventory recovery snapshot")
                entries.append((target, old))
            current = hashlib.sha256(_safe_path(self.root / "inventory.json").read_bytes()).hexdigest()
            if current not in (journal["before"], journal["after"]):
                raise ValueError("Inventory recovery cannot identify the JSON commit point")
            if current == journal["before"]:
                for target, old in entries:
                    if old is None:
                        target.unlink(missing_ok=True)
                        if target.parent.exists():
                            _fsync_dir(target.parent)
                    else:
                        _write_atomic(target, _safe_path(directory / old).read_bytes())
                backup.unlink(missing_ok=True)
                _fsync_dir(backup.parent)
            journal_path.unlink()
            _fsync_dir(self.root)
            shutil.rmtree(directory)
            _fsync_dir(self.root)
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid inventory recovery journal") from exc

    def verify(self):
        with self._locked():
            inventory, _ = self._load()
            registered = {f.path for g in inventory.garments for f in g.asset.files}
            orphans = []
            for path in sorted((self.root / "assets").rglob("*")):
                relative = str(path.relative_to(self.root))
                _safe_path(path)
                if path.is_file() and relative not in registered:
                    orphans.append(relative)
            orphans.extend(p.name for p in sorted(self.root.glob(".transaction-*")))
            orphans.extend(p.name for p in sorted(self.root.glob(".inventory-*.tmp")))
            return {"status": "ok", "garments": len(inventory.garments), "orphans": orphans}


class _Transaction:
    def __init__(self, store, inventory):
        self.store = store
        self.inventory = inventory
        self.staged = {}
        self.directory = None
        self._snapshot_number = 0

    def ensure_directory(self):
        if self.directory is None:
            self.directory = Path(tempfile.mkdtemp(prefix=".transaction-", dir=self.store.root))
            _fsync_dir(self.store.root)

    def stage_asset(self, number, role, source_path, *, extension=None):
        from mascot_wardrobe.domain.inventory import AssetFile

        if type(number) is not int or number < 1:
            raise ValueError("Asset number must be a positive integer")
        if not isinstance(role, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", role) is None:
            raise ValueError("Unsafe inventory asset role")
        source = _safe_path(source_path)
        if not source.is_file():
            raise ValueError("Asset source must be a regular file")
        extension = source.suffix.lower().lstrip(".") if extension is None else extension.lstrip(".").lower()
        if re.fullmatch(r"[a-z0-9]{1,16}", extension) is None:
            raise ValueError("Unsafe inventory asset extension")
        relative = f"assets/{number}/{role}.{extension}"
        self.store._relative(relative)
        self.ensure_directory()
        snapshot = self.directory / f"new-{self._snapshot_number}.{extension}"
        self._snapshot_number += 1
        content = source.read_bytes()
        _write_atomic(snapshot, content)
        self.staged[relative] = snapshot
        return AssetFile(role=role, path=relative, sha256=hashlib.sha256(content).hexdigest())

    def staged_asset_path(self, asset_file):
        """Expose the copied snapshot so adapters validate exactly the bytes being committed."""
        path = self.staged.get(asset_file.path)
        if path is None or not _safe_path(path).is_file():
            raise ValueError("Asset is not staged in this transaction")
        if hashlib.sha256(path.read_bytes()).hexdigest() != asset_file.sha256:
            raise ValueError("Asset does not match its staged snapshot")
        return path

    def cleanup(self):
        if self.directory is not None and self.directory.exists():
            # A failed recovery must retain its snapshots for a later attempt.
            if not (self.store.root / ".journal.json").exists():
                shutil.rmtree(self.directory)
                _fsync_dir(self.store.root)
