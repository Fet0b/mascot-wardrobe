"""Transactional inventory persistence contracts."""

import json
import shutil
from pathlib import Path

import pytest

from mascot_wardrobe.infrastructure.inventory_store import InventoryStore, data_home


def test_data_home_precedence_and_safe_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.delenv("MASCOT_WARDROBE_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert data_home() == tmp_path / "user" / ".local/share/mascot-wardrobe"
    monkeypatch.setenv("XDG_DATA_HOME", "relative")
    assert data_home() == tmp_path / "user" / ".local/share/mascot-wardrobe"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert data_home() == tmp_path / "xdg/mascot-wardrobe"
    monkeypatch.setenv("MASCOT_WARDROBE_HOME", str(tmp_path / "env"))
    assert data_home() == tmp_path / "env"
    assert data_home(tmp_path / "explicit") == tmp_path / "explicit"
    with pytest.raises(ValueError, match="traversal|symbolic"):
        data_home(tmp_path / "child/../escape")
    (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        data_home(tmp_path / "link/data")


def test_first_open_and_noop_have_no_backup_or_rewrite(tmp_path):
    store = InventoryStore.open(tmp_path / "home")
    assert store.root == tmp_path / "home/inventory"
    assert store.home == tmp_path / "home"
    assert store.read().next_number == 1
    path = store.root / "inventory.json"
    before, modified = path.read_bytes(), path.stat().st_mtime_ns
    with store.transaction():
        pass
    assert (path.read_bytes(), path.stat().st_mtime_ns) == (before, modified)
    assert not list((store.root / "backups").iterdir())
    assert store.verify()["orphans"] == []


def _add(store, tmp_path, content=b"asset", name="Coat"):
    source = tmp_path / "source.png"
    source.write_bytes(content)
    from mascot_wardrobe.domain.inventory import AssetRef

    with store.transaction() as transaction:
        asset = transaction.stage_asset(transaction.inventory.next_number, "portrait", source)
        transaction.inventory, garment = transaction.inventory.add(
            name=name,
            asset=AssetRef("png", (asset,)),
            origin={"kind": "asset", "source_sha256": asset.sha256},
            ranges=("cold",),
        )
    return garment


@pytest.mark.parametrize("evidence", ["assets", "backups", "transaction"])
def test_missing_metadata_never_reinitializes_an_existing_inventory(tmp_path, evidence):
    store = InventoryStore.open(tmp_path / "home")
    _add(store, tmp_path)
    (store.root / "inventory.json").unlink()
    if evidence != "assets":
        shutil.rmtree(store.root / "assets")
        (store.root / "assets").mkdir()
    if evidence != "backups":
        shutil.rmtree(store.root / "backups")
        (store.root / "backups").mkdir()
    if evidence == "transaction":
        (store.root / ".transaction-interrupted").mkdir()
    before = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="metadata.*missing|missing.*metadata"):
        InventoryStore.open(store.home)
    assert not (store.root / "inventory.json").exists()
    assert {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()} == before


def test_staged_snapshot_commit_backup_hash_and_tamper(tmp_path):
    store = InventoryStore.open(tmp_path / "home")
    before = (store.root / "inventory.json").read_bytes()
    garment = _add(store, tmp_path)
    assert garment.number == 1
    assert store.read().describe(1) == garment
    assert store.asset_path(garment.asset.files[0]).read_bytes() == b"asset"
    assert (store.root / "backups/inventory-1.json").read_bytes() == before
    assert store.verify()["orphans"] == []
    store.asset_path(garment.asset.files[0]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="sha256"):
        store.read()


def test_aborted_stage_and_failed_commit_preserve_previous_bytes(tmp_path, monkeypatch):
    from mascot_wardrobe.domain.inventory import AssetRef

    store = InventoryStore.open(tmp_path / "home")
    garment = _add(store, tmp_path)
    before = (store.root / "inventory.json").read_bytes()
    backups = sorted((store.root / "backups").iterdir())
    source = tmp_path / "changed.png"
    source.write_bytes(b"changed")
    with pytest.raises(KeyboardInterrupt):
        with store.transaction() as transaction:
            transaction.stage_asset(1, "portrait", source)
            raise KeyboardInterrupt
    assert (store.root / "inventory.json").read_bytes() == before
    assert store.asset_path(garment.asset.files[0]).read_bytes() == b"asset"
    assert store.verify()["orphans"] == []

    original = __import__("mascot_wardrobe.infrastructure.inventory_store", fromlist=["_"])._write_atomic

    def fail_json(path, data):
        if Path(path).name == "inventory.json":
            raise OSError("injected disk failure")
        return original(path, data)

    monkeypatch.setattr("mascot_wardrobe.infrastructure.inventory_store._write_atomic", fail_json)
    with pytest.raises(OSError, match="injected"):
        with store.transaction() as transaction:
            asset = transaction.stage_asset(1, "portrait", source)
            transaction.inventory = transaction.inventory.replace_asset(1, AssetRef("png", (asset,)))
    assert (store.root / "inventory.json").read_bytes() == before
    assert store.asset_path(garment.asset.files[0]).read_bytes() == b"asset"
    assert sorted((store.root / "backups").iterdir()) == backups
    assert store.verify()["orphans"] == []


def test_schema_refusal_migration_backup_and_safe_internal_paths(tmp_path, monkeypatch):
    from mascot_wardrobe.infrastructure import inventory_store

    store = InventoryStore.open(tmp_path / "home")
    path = store.root / "inventory.json"
    data = json.loads(path.read_bytes())
    data["schema_version"] = 99
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="schema"):
        store.read()
    data["schema_version"] = 0
    raw = json.dumps(data).encode()
    path.write_bytes(raw)
    monkeypatch.setitem(inventory_store.MIGRATIONS, 0, lambda old: {**old, "schema_version": 1})
    assert store.read().schema_version == 1
    assert (store.root / "backups/inventory-1.json").read_bytes() == raw
    path.unlink()
    external = tmp_path / "external.json"
    external.write_text(json.dumps({**data, "schema_version": 1}))
    path.symlink_to(external)
    with pytest.raises(ValueError, match="symbolic"):
        store.read()


def _python(script, *arguments):
    import os
    import subprocess
    import sys

    environment = dict(os.environ)
    environment.pop("OPENAI_API_KEY", None)
    return subprocess.run(
        [sys.executable, "-c", script, *map(str, arguments)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("point", ["asset", "json"])
def test_process_crash_recovers_according_to_json_commit_point(tmp_path, point):
    store = InventoryStore.open(tmp_path / "home")
    garment = _add(store, tmp_path)
    before = (store.root / "inventory.json").read_bytes()
    backup_count = len(list((store.root / "backups").iterdir()))
    changed = tmp_path / "changed.png"
    changed.write_bytes(b"changed")
    script = """
import os, sys
from pathlib import Path
from mascot_wardrobe.domain.inventory import AssetRef
from mascot_wardrobe.infrastructure import inventory_store as module
store = module.InventoryStore.open(sys.argv[1])
if sys.argv[3] == "asset":
    original = module.InventoryStore._install_asset
    def install(self, staged, target):
        original(self, staged, target)
        os._exit(77)
    module.InventoryStore._install_asset = install
else:
    original = module._write_atomic
    def write(path, data):
        original(path, data)
        if Path(path).name == "inventory.json":
            os._exit(77)
    module._write_atomic = write
with store.transaction() as tx:
    file = tx.stage_asset(1, "portrait", sys.argv[2])
    tx.inventory = tx.inventory.replace_asset(1, AssetRef("png", (file,)))
"""
    result = _python(script, store.home, changed, point)
    assert result.returncode == 77, result.stderr
    recovered = InventoryStore.open(store.home)
    assert recovered.verify()["orphans"] == []
    if point == "asset":
        assert (store.root / "inventory.json").read_bytes() == before
        assert store.asset_path(garment.asset.files[0]).read_bytes() == b"asset"
        assert len(list((store.root / "backups").iterdir())) == backup_count
    else:
        assert recovered.read().describe(1).version == 2
        assert recovered.asset_path(recovered.read().describe(1).asset.files[0]).read_bytes() == b"changed"
        assert len(list((store.root / "backups").iterdir())) == backup_count + 1


def test_process_transactions_serialize_and_reload_numbers(tmp_path):
    import os
    import subprocess
    import sys

    store = InventoryStore.open(tmp_path / "home")
    script = """
import sys, time
from mascot_wardrobe.domain.inventory import AssetRef
from mascot_wardrobe.infrastructure.inventory_store import InventoryStore
store = InventoryStore.open(sys.argv[1])
with store.transaction() as tx:
    time.sleep(.1)
    file = tx.stage_asset(tx.inventory.next_number, "portrait", sys.argv[2])
    tx.inventory, garment = tx.inventory.add(
        name="Coat", asset=AssetRef("png", (file,)),
        origin={"kind":"asset", "source_sha256": file.sha256}, ranges=("cold",))
print(garment.number)
"""
    environment = dict(os.environ)
    environment.pop("OPENAI_API_KEY", None)
    children = []
    for index in range(2):
        source = tmp_path / f"source-{index}.png"
        source.write_bytes(str(index).encode())
        children.append(
            subprocess.Popen(
                [sys.executable, "-c", script, str(store.home), str(source)],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    numbers = []
    for child in children:
        stdout, stderr = child.communicate(timeout=20)
        assert child.returncode == 0, stderr
        numbers.append(int(stdout))
    assert sorted(numbers) == [1, 2]
    assert store.read().next_number == 3
    assert len(store.read().garments) == 2


@pytest.mark.parametrize("name", ["assets", "backups", "inventory.lock"])
def test_internal_symbolic_links_are_rejected(tmp_path, name):
    store = InventoryStore.open(tmp_path / "home")
    target = tmp_path / "external"
    path = store.root / name
    if path.is_dir():
        path.rmdir()
        target.mkdir()
    else:
        path.unlink()
        target.write_bytes(b"keep")
    path.symlink_to(target, target_is_directory=target.is_dir())
    with pytest.raises(ValueError, match="symbolic"):
        InventoryStore.open(store.home)


def test_orphan_report_and_unused_staging_cleanup(tmp_path):
    store = InventoryStore.open(tmp_path / "home")
    source = tmp_path / "source.png"
    source.write_bytes(b"first")
    before = (store.root / "inventory.json").read_bytes()
    with store.transaction() as transaction:
        transaction.stage_asset(1, "portrait", source)
    assert store.verify()["orphans"] == []
    assert (store.root / "inventory.json").read_bytes() == before
    orphan = store.root / "assets/99/portrait.png"
    orphan.parent.mkdir()
    orphan.write_bytes(b"orphan")
    assert store.verify()["orphans"] == ["assets/99/portrait.png"]
    assert orphan.read_bytes() == b"orphan"


def test_repeated_staging_keeps_distinct_latest_snapshots(tmp_path):
    from mascot_wardrobe.domain.inventory import AssetRef

    store = InventoryStore.open(tmp_path / "home")
    source = tmp_path / "source.png"
    source.write_bytes(b"first")
    with store.transaction() as transaction:
        transaction.stage_asset(1, "portrait", source)
        source.write_bytes(b"latest")
        portrait = transaction.stage_asset(1, "portrait", source)
        source.write_bytes(b"preview")
        preview = transaction.stage_asset(1, "preview", source, extension="jpg")
        transaction.inventory, garment = transaction.inventory.add(
            name="Coat",
            asset=AssetRef("png", (portrait, preview)),
            origin={"kind": "asset", "source_sha256": portrait.sha256},
            ranges=("cold",),
        )
    assert store.asset_path(garment.asset.files[0]).read_bytes() == b"latest"
    assert store.asset_path(garment.asset.files[1]).read_bytes() == b"preview"


def test_staged_asset_path_exposes_only_matching_immutable_snapshot(tmp_path):
    from mascot_wardrobe.domain.inventory import AssetFile

    store = InventoryStore.open(tmp_path / "home")
    source = tmp_path / "source.png"
    source.write_bytes(b"original")
    with store.transaction() as transaction:
        asset = transaction.stage_asset(1, "portrait", source)
        snapshot = transaction.staged_asset_path(asset)
        assert snapshot.suffix == ".png"
        source.write_bytes(b"changed later")
        assert snapshot.read_bytes() == b"original"
        wrong = AssetFile(role="portrait", path=asset.path, sha256="a" * 64)
        with pytest.raises(ValueError, match="staged"):
            transaction.staged_asset_path(wrong)
