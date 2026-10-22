"""Inventory imports preserve identity, provenance and atomic registration."""

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from mascot_wardrobe.domain.models import Category


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def png(path, color="red"):
    Image.new("RGBA", (8, 8), color).save(path)
    return path


def catalog(root):
    root.mkdir()
    outfits = []
    for index, category in enumerate(Category):
        slug = category.value.replace("_", "-")
        paths = {"png": f"{slug}.png", "jpg": f"{slug}.jpg", "atlas": f"{slug}-atlas.png"}
        for name in paths.values():
            Image.new("RGB", (8, 8), (index * 30, 20, 40)).save(root / name)
        outfits.append(
            {
                "slug": slug,
                "category": category.value,
                **paths,
                "sha256": {key: digest(root / name) for key, name in paths.items()},
            }
        )
    data = {
        "mascot_id": "pet",
        "country_code": "BR",
        "outfits": outfits,
        "request": {
            "source_sha256": "a" * 64,
            "thresholds_c": [10, 18, 24, 29, 34],
            "style": "streetwear",
            "style_choice": "user",
            "palette": ["#123456"],
            "palette_choice": "user",
            "renderer_config": {"templates": 2},
        },
    }
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps(data))
    return manifest


def store_at(tmp_path):
    from mascot_wardrobe.infrastructure.inventory_store import InventoryStore

    return InventoryStore.open(tmp_path / "data")


def test_manual_import_is_content_addressed_and_rejects_unsupported_format(tmp_path):
    from mascot_wardrobe.application.inventory import register_asset

    store = store_at(tmp_path)
    source = png(tmp_path / "portrait.png")
    first = register_asset(source, store, name="Red coat", ranges=("cold",), palette=["#ff0000"])
    assert first == {"number": 1, "status": "imported"}
    before = (store.root / "inventory.json").read_bytes()
    assert register_asset(source, store, name="Different label", ranges=("warm",)) == {
        "number": 1,
        "status": "unchanged",
    }
    assert (store.root / "inventory.json").read_bytes() == before
    assert not store.read().defaults
    with pytest.raises(ValueError, match="not supported"):
        register_asset(source, store, name="Bad", ranges=("cold",), fmt="gif")


def test_manual_import_requires_a_range_without_mutating_inventory(tmp_path):
    from mascot_wardrobe.application.inventory import register_asset

    store = store_at(tmp_path)
    source = png(tmp_path / "portrait.png")
    before = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="[Rr]ange"):
        register_asset(source, store, name="Coat", ranges=())
    assert {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()} == before
    assert store.read().next_number == 1
    assert store.verify()["orphans"] == []


def test_manifest_registers_six_preserves_provenance_and_is_idempotent(tmp_path):
    from mascot_wardrobe.application.inventory import register_manifest

    store = store_at(tmp_path)
    manifest = catalog(tmp_path / "catalog")
    assert register_manifest(manifest, store) == {"imported": [1, 2, 3, 4, 5, 6], "skipped": []}
    inventory = store.read()
    assert len(inventory.garments) == 6
    assert not inventory.defaults
    for garment, category in zip(inventory.garments, Category, strict=True):
        assert garment.ranges == (category.value,)
        assert garment.style == "streetwear"
        assert garment.origin["renderer_config"]["templates"] == 2
        assert garment.origin["manifest_sha256"] == digest(manifest)
        assert {asset.role for asset in garment.asset.files} == {"portrait", "preview", "atlas"}
    before = (store.root / "inventory.json").read_bytes()
    assert register_manifest(manifest, store) == {"imported": [], "skipped": [1, 2, 3, 4, 5, 6]}
    assert (store.root / "inventory.json").read_bytes() == before


@pytest.mark.parametrize("companion", ["jpg", "atlas"])
def test_manifest_same_identity_changed_companion_aborts_entire_batch(tmp_path, companion):
    from mascot_wardrobe.application.inventory import register_manifest

    store = store_at(tmp_path)
    manifest = catalog(tmp_path / "catalog")
    register_manifest(manifest, store)
    data = json.loads(manifest.read_text())
    # Stage a new identity before reaching the conflicting existing garment.
    first, conflict = data["outfits"][:2]
    for outfit, role in ((first, "png"), (conflict, companion)):
        path = manifest.parent / outfit[role]
        Image.new("RGB", (8, 8), "green").save(path)
        outfit["sha256"][role] = digest(path)
    manifest.write_text(json.dumps(data))
    before = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="collision.*garment #2"):
        register_manifest(manifest, store)
    assert {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()} == before
    assert store.read().next_number == 7
    assert store.verify()["orphans"] == []


@pytest.mark.parametrize(
    "mutation",
    ["hash", "count", "duplicate", "slug", "symlink", "oversize", "not-png", "jpg-hash", "atlas-hash"],
)
def test_unsafe_manifest_aborts_without_modifying_existing_inventory(tmp_path, mutation):
    from mascot_wardrobe.application.inventory import register_asset, register_manifest

    store = store_at(tmp_path)
    register_asset(png(tmp_path / "existing.png"), store, name="Existing", ranges=("cold",))
    manifest = catalog(tmp_path / "catalog")
    data = json.loads(manifest.read_text())
    first = data["outfits"][0]
    if mutation.endswith("hash"):
        role = {"jpg-hash": "jpg", "atlas-hash": "atlas"}.get(mutation, "png")
        first["sha256"][role] = "f" * 64
    elif mutation == "count":
        data["outfits"].pop()
    elif mutation == "duplicate":
        data["outfits"][1]["category"] = first["category"]
    elif mutation == "slug":
        first["slug"] = "../escape"
    elif mutation == "symlink":
        path = manifest.parent / first["png"]
        path.unlink()
        path.symlink_to(tmp_path / "existing.png")
    elif mutation == "not-png":
        path = manifest.parent / first["png"]
        Image.new("RGB", (8, 8)).save(path, format="JPEG")
        first["sha256"]["png"] = digest(path)
    manifest.write_text(json.dumps(data) if mutation != "oversize" else " " * 262145)
    before = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        register_manifest(manifest, store)
    assert {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()} == before


def test_manifest_batch_staging_failure_is_atomic(tmp_path, monkeypatch):
    from mascot_wardrobe.application.inventory import register_asset, register_manifest
    from mascot_wardrobe.infrastructure.inventory_store import InventoryStore

    store = store_at(tmp_path)
    register_asset(png(tmp_path / "existing.png"), store, name="Existing", ranges=("cold",))
    manifest = catalog(tmp_path / "catalog")
    before = (store.root / "inventory.json").read_bytes()
    original_transaction = InventoryStore.transaction
    from contextlib import contextmanager

    @contextmanager
    def failing_transaction(self):
        with original_transaction(self) as tx:
            original = tx.stage_asset
            count = 0

            def fail(*args, **kwargs):
                nonlocal count
                count += 1
                if count == 5:
                    raise OSError("injected staging failure")
                return original(*args, **kwargs)

            tx.stage_asset = fail
            yield tx

    monkeypatch.setattr(InventoryStore, "transaction", failing_transaction)
    with pytest.raises(OSError, match="injected"):
        register_manifest(manifest, store)
    assert (store.root / "inventory.json").read_bytes() == before
    assert store.read().next_number == 2


def make_closet(tmp_path, root="closet", *, palette=None):
    from mascot_wardrobe.closet import Closet
    from tests.test_closet import write_mascot, write_mask

    source, mask = tmp_path / "mascot.png", tmp_path / "mask.png"
    write_mascot(source)
    write_mask(mask)
    closet = Closet.init(tmp_path / root, source, mask)
    closet.add_item("coat", template="cold", palette=palette)
    return closet


def test_closet_identity_metadata_ranges_and_guarded_replacement(tmp_path):
    from mascot_wardrobe.application.inventory import register_closet_item

    store = store_at(tmp_path)
    closet = make_closet(tmp_path)
    first = register_closet_item(closet, "coat", store, ranges=("warm",))
    assert first == {"number": 1, "status": "imported"}
    assert closet.mascot_sha256 == digest(closet.source_path)
    assert closet.mask_sha256 == digest(closet.mask_path)
    old = closet.items()["coat"]["sha256"]
    closet.edit_item("coat", name="Renamed")
    assert register_closet_item(closet, "coat", store) == {"number": 1, "status": "updated"}
    item = store.read().describe(1)
    assert (item.name, item.version, item.ranges) == ("Renamed", 1, ("cold", "warm"))
    closet.edit_item("coat", palette=["#ff0000"])
    before = (store.root / "inventory.json").read_bytes()
    with pytest.raises(ValueError, match="collision.*garment #1"):
        register_closet_item(closet, "coat", store)
    assert (store.root / "inventory.json").read_bytes() == before
    with pytest.raises(ValueError, match="collision"):
        register_closet_item(closet, "coat", store, replaces="f" * 64)
    assert register_closet_item(closet, "coat", store, replaces=old) == {"number": 1, "status": "updated"}
    item = store.read().describe(1)
    assert item.version == 2
    assert item.origin["history"] == (old,)
    assert item.ranges == ("cold", "warm")


def test_two_closets_same_identity_different_bytes_fail_without_changes(tmp_path):
    from mascot_wardrobe.application.inventory import (
        closet_item_id_free,
        register_closet,
        require_closet_item_id_free,
    )

    store = store_at(tmp_path)
    first = make_closet(tmp_path, "first")
    second = make_closet(tmp_path, "second", palette=["#ff0000"])
    assert register_closet(first, store) == {"imported": [1], "skipped": []}
    before = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    assert not closet_item_id_free(second, "coat", store)
    with pytest.raises(ValueError, match="collision.*coat.*garment #1.*different item id"):
        require_closet_item_id_free(second, "coat", store)
    with pytest.raises(ValueError, match="collision"):
        register_closet(second, store)
    assert {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()} == before


def test_closet_single_item_retry_and_migration_guard(tmp_path):
    from mascot_wardrobe.application.inventory import register_closet, register_closet_item

    store = store_at(tmp_path)
    closet = make_closet(tmp_path)
    register_closet(closet, store)
    previous = closet.items()["coat"]["sha256"]
    closet.edit_item("coat", palette=["#ff0000"])
    with pytest.raises(ValueError, match="--item"):
        register_closet(closet, store, replaces=previous)
    assert register_closet(closet, store, item_id="coat", replaces=previous) == {
        "imported": [],
        "skipped": [],
        "updated": [1],
    }
    previous = closet.items()["coat"]["sha256"]
    closet.edit_item("coat", palette=["#0000ff"])
    closet._data["items"]["coat"]["migrated_from"] = {"sha256": previous}
    assert register_closet(closet, store) == {"imported": [], "skipped": [], "updated": [1]}
    assert store.read().describe(1).version == 3
    assert register_closet_item(closet, "coat", store)["status"] == "unchanged"


def test_explicit_first_registration_recovery_keeps_existing_hash_guards(tmp_path):
    from mascot_wardrobe.application.inventory import register_closet

    store = store_at(tmp_path)
    closet = make_closet(tmp_path)
    previous = closet.items()["coat"]["sha256"]
    with pytest.raises(ValueError, match="unregistered"):
        register_closet(closet, store, item_id="coat", replaces=previous)
    assert register_closet(closet, store, item_id="coat", replaces=previous, allow_first_registration=True)[
        "imported"
    ] == [1]
    closet.edit_item("coat", style="streetwear")
    before = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="collision"):
        register_closet(closet, store, item_id="coat", replaces="f" * 64, allow_first_registration=True)
    assert {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()} == before
    assert register_closet(closet, store, item_id="coat", replaces=previous, allow_first_registration=True)[
        "updated"
    ] == [1]
    assert store.read().describe(1).version == 2


@pytest.mark.parametrize("options", [{}, {"item_id": "coat"}, {"replaces": "a" * 64}])
def test_first_registration_recovery_rejects_missing_scope_or_guard(tmp_path, options):
    from mascot_wardrobe.application.inventory import register_closet

    store = store_at(tmp_path)
    closet = make_closet(tmp_path)
    with pytest.raises(ValueError, match="--item"):
        register_closet(closet, store, allow_first_registration=True, **options)
    assert store.read().next_number == 1


def test_closet_import_ranges_require_single_item(tmp_path):
    from mascot_wardrobe.application.inventory import register_closet

    store = store_at(tmp_path)
    closet = make_closet(tmp_path)
    with pytest.raises(ValueError, match="--range requires --item"):
        register_closet(closet, store, ranges=("mild",))
    assert store.read().next_number == 1


def test_manifest_long_safe_slug_gets_valid_display_name(tmp_path):
    from mascot_wardrobe.application.inventory import register_manifest

    store = store_at(tmp_path)
    manifest = catalog(tmp_path / "catalog")
    data = json.loads(manifest.read_text())
    outfit = data["outfits"][0]
    slug = "long-" + "a" * 90
    for key, suffix in (("png", ".png"), ("jpg", ".jpg"), ("atlas", "-atlas.png")):
        (manifest.parent / outfit[key]).rename(manifest.parent / (slug + suffix))
        outfit[key] = slug + suffix
    outfit["slug"] = slug
    manifest.write_text(json.dumps(data))
    register_manifest(manifest, store)
    assert store.read().describe(1).name == slug.replace("-", " ")[:60]


@pytest.mark.parametrize("kind", ["asset", "manifest"])
def test_import_validates_copied_snapshot_after_source_changes(tmp_path, monkeypatch, kind):
    from mascot_wardrobe.application import inventory as use_cases

    store = store_at(tmp_path)
    if kind == "asset":
        source = png(tmp_path / "source.png")

        def call():
            return use_cases.register_asset(source, store, name="Changed", ranges=("cold",))
    else:
        manifest = catalog(tmp_path / "catalog")
        data = json.loads(manifest.read_text())
        for outfit in data["outfits"]:
            outfit.pop("sha256")
        manifest.write_text(json.dumps(data))
        source = manifest.parent / data["outfits"][0]["png"]

        def call():
            return use_cases.register_manifest(manifest, store)

    original = use_cases._sha

    def changed_hash(path):
        if path == source:
            Image.new("RGB", (8, 8)).save(source, format="JPEG")
        return original(path)

    monkeypatch.setattr(use_cases, "_sha", changed_hash)
    before = (store.root / "inventory.json").read_bytes()
    with pytest.raises(ValueError, match="PNG"):
        call()
    assert (store.root / "inventory.json").read_bytes() == before
    assert not store.read().garments


def test_manifest_read_is_bounded_even_if_file_grows_after_stat(tmp_path, monkeypatch):
    import io

    from mascot_wardrobe.application.inventory import register_manifest

    store = store_at(tmp_path)
    manifest = catalog(tmp_path / "catalog")
    original_open = Path.open
    reads = []

    class GrowingReader(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    def fake_open(path, *args, **kwargs):
        if path == manifest and (args[0] if args else kwargs.get("mode")) == "rb":
            return GrowingReader(b" " * 262145)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)
    with pytest.raises(ValueError, match="too large"):
        register_manifest(manifest, store)
    assert reads == [262145]


@pytest.mark.parametrize("palette", [42, {"#123456": "unexpected object"}, "#123456"])
def test_malformed_manifest_palette_is_a_value_error_and_atomic(tmp_path, palette):
    from mascot_wardrobe.application.inventory import register_manifest

    store = store_at(tmp_path)
    manifest = catalog(tmp_path / "catalog")
    data = json.loads(manifest.read_text())
    data["request"]["palette"] = palette
    manifest.write_text(json.dumps(data))
    before = (store.root / "inventory.json").read_bytes()
    with pytest.raises(ValueError, match="[Pp]alette"):
        register_manifest(manifest, store)
    assert (store.root / "inventory.json").read_bytes() == before


def test_library_guarded_edit_can_first_register_legacy_item_but_retry_needs_existing_key(tmp_path):
    from mascot_wardrobe.application.inventory import register_closet, register_closet_item

    store = store_at(tmp_path)
    closet = make_closet(tmp_path)
    old = closet.items()["coat"]["sha256"]
    closet.edit_item("coat", palette=["#ff0000"])
    before = (store.root / "inventory.json").read_bytes()
    with pytest.raises(ValueError, match="unregistered"):
        register_closet(closet, store, item_id="coat", replaces=old)
    assert (store.root / "inventory.json").read_bytes() == before
    assert register_closet_item(closet, "coat", store, replaces=old) == {"number": 1, "status": "imported"}
    assert store.read().describe(1).version == 1


@pytest.mark.parametrize("name", ["portrait", "misleading.jpg"])
def test_manual_png_import_uses_adapter_extension_not_source_suffix(tmp_path, name):
    from mascot_wardrobe.application.inventory import register_asset

    store = store_at(tmp_path)
    source = tmp_path / name
    Image.new("RGBA", (8, 8), "red").save(source, format="PNG")
    register_asset(source, store, name="Portrait", ranges=("cold",))
    asset = store.read().describe(1).asset.files[0]
    assert asset.path.endswith("/portrait.png")


def test_closet_batch_metadata_refresh_reports_updated_and_repeat_reports_skipped(tmp_path):
    from mascot_wardrobe.application.inventory import register_closet

    store = store_at(tmp_path)
    closet = make_closet(tmp_path)
    assert register_closet(closet, store) == {"imported": [1], "skipped": []}
    closet.edit_item("coat", name="New label")
    assert register_closet(closet, store) == {"imported": [], "skipped": [], "updated": [1]}
    assert store.read().describe(1).name == "New label"
    assert store.read().describe(1).version == 1
    before = (store.root / "inventory.json").read_bytes()
    assert register_closet(closet, store) == {"imported": [], "skipped": [1]}
    assert (store.root / "inventory.json").read_bytes() == before


@pytest.mark.parametrize("revision, items_dir", [(2, "items"), (3, "items"), (3, "items-t3")])
def test_real_closet_migration_repins_only_matching_inventory_hash(tmp_path, revision, items_dir):
    from mascot_wardrobe.application.inventory import register_closet
    from mascot_wardrobe.closet import Closet

    store = store_at(tmp_path)
    closet = make_closet(tmp_path)
    path = closet.item_path("coat")
    old = Image.open(path).convert("RGBA")
    old.putpixel((40, 120), (1, 2, 3, 255))  # a valid masked pixel from a prior renderer
    old.save(path)
    data = json.loads((closet.root / "closet.json").read_text())
    data["items"]["coat"]["sha256"] = digest(path)
    (closet.root / "closet.json").write_text(json.dumps(data))
    closet = Closet.open(closet.root)
    register_closet(closet, store, item_id="coat", ranges=("warm",))
    previous = closet.items()["coat"]["sha256"]
    data["renderer"] = {"renderer": "local-composer", "templates": revision}
    if revision == 3:
        data["renderer"]["styles"] = ["classic", "streetwear"]
    if items_dir != "items":
        (closet.root / "items").rename(closet.root / items_dir)
        data["items_dir"] = items_dir
        data["items"]["coat"]["file"] = f"{items_dir}/coat.png"
    (closet.root / "closet.json").write_text(json.dumps(data))
    Closet.migrate(closet.root)
    migrated = Closet.open(closet.root)
    assert migrated.items()["coat"]["migrated_from"]["sha256"] == previous
    before = (store.root / "inventory.json").read_bytes()
    migrated._data["items"]["coat"]["migrated_from"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="collision"):
        register_closet(migrated, store)
    assert (store.root / "inventory.json").read_bytes() == before
    migrated._data["items"]["coat"]["migrated_from"]["sha256"] = previous
    assert register_closet(migrated, store) == {"imported": [], "skipped": [], "updated": [1]}
    garment = store.read().describe(1)
    assert garment.version == 2
    assert "warm" in garment.ranges
    assert register_closet(migrated, store) == {"imported": [], "skipped": [1]}
