"""Pure inventory behavior and persistence shape."""

import dataclasses

import pytest

from mascot_wardrobe.domain.inventory import (
    AssetFile,
    AssetRef,
    Inventory,
    inventory_from_dict,
    inventory_to_dict,
    origin_key,
)

HASH = "a" * 64
OTHER = "b" * 64


def asset(digest=HASH, number=1):
    return AssetRef("opaque-future-format", (AssetFile("portrait", f"assets/{number}/portrait.png", digest),))


def add(inventory, digest=HASH, **kwargs):
    fields = dict(
        name="Coat",
        asset=asset(digest, inventory.next_number),
        origin={"kind": "asset", "source_sha256": digest},
        ranges=("cold",),
    )
    fields.update(kwargs)
    return inventory.add(**fields)


def test_allocation_retirement_and_roundtrip_never_reuse_numbers():
    empty = Inventory()
    first, garment = add(empty)
    assert garment.number == 1 and garment.version == 1
    assert empty.next_number == 1 and not empty.garments
    removed = first.remove(1)
    assert removed.retired == (1,)
    loaded = inventory_from_dict(inventory_to_dict(removed))
    second, garment = add(loaded, OTHER)
    assert garment.number == 2 and second.next_number == 3


def test_defaults_ignore_suitability_and_protect_mapped_or_active_numbers():
    inventory, garment = add(Inventory())
    mapped = inventory.set_default("very_hot", garment.number)
    assert mapped.default_for("very_hot") == garment.number
    assert inventory.default_for("very_hot") is None
    with pytest.raises(ValueError, match="very_hot"):
        mapped.remove(garment.number)
    with pytest.raises(ValueError, match="active"):
        dataclasses.replace(inventory, active=garment.number).remove(garment.number)
    with pytest.raises(ValueError):
        inventory.set_default("unknown", garment.number)
    with pytest.raises(ValueError):
        inventory.set_default("cold", 999)
    with pytest.raises(ValueError):
        inventory.describe(True)


def test_origin_lookup_and_replacement_preserve_identity_and_history():
    origin = {"kind": "closet", "mascot_sha256": HASH, "mask_sha256": OTHER, "item": "coat", "history": []}
    inventory, garment = add(Inventory(), origin=origin)
    assert inventory.find_by_origin_key(origin_key(origin)) == garment
    assert not inventory.closet_item_id_free(HASH, OTHER, "coat")
    assert inventory.closet_item_id_free(HASH, OTHER, "other")
    changed = inventory.replace_asset(1, asset(OTHER))
    current = changed.describe(1)
    assert current.number == garment.number and current.version == 2
    assert list(current.origin["history"]) == [HASH]
    assert current.asset.files[0].sha256 == OTHER
    assert inventory.describe(1).version == 1


@pytest.mark.parametrize(
    "fields",
    [
        {"name": ""},
        {"name": "x" * 61},
        {"name": "two\nlines"},
        {"name": "hidden\u200b"},
        {"palette": ("red",)},
        {"palette": ("#123456",) * 7},
        {"ranges": ("snowy",)},
        {"ranges": ()},
        {"ranges": []},
        {"version": True},
        {"origin": {"kind": "unknown"}},
        {"origin": {"kind": "asset", "source_sha256": "invalid"}},
        {"origin": {"kind": "asset", "source_sha256": HASH, "bad": float("nan")}},
    ],
)
def test_garment_rejects_invalid_metadata(fields):
    with pytest.raises(ValueError):
        add(Inventory(), **fields)


def test_omitted_or_persisted_empty_ranges_are_rejected():
    with pytest.raises(ValueError, match="[Rr]ange"):
        Inventory().add(name="Coat", asset=asset(), origin={"kind": "asset", "source_sha256": HASH})
    inventory, _ = add(Inventory())
    data = inventory_to_dict(inventory)
    data["garments"][0]["ranges"] = []
    with pytest.raises(ValueError, match="[Rr]ange"):
        inventory_from_dict(data)


@pytest.mark.parametrize(
    "fields",
    [
        {"path": "../escape"},
        {"path": "/absolute"},
        {"path": "assets/../escape"},
        {"path": "assets\\escape"},
        {"path": "assets//portrait.png"},
        {"sha256": "oops"},
        {"role": ""},
    ],
)
def test_asset_file_rejects_unsafe_value_fields(fields):
    with pytest.raises(ValueError):
        AssetFile(**{**dict(role="portrait", path="assets/1/portrait.png", sha256=HASH), **fields})


@pytest.mark.parametrize(
    "fields",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"next_number": True},
        {"next_number": 1},
        {"retired": (1,)},
        {"retired": (2, 2)},
        {"defaults": {"snowy": 1}},
        {"defaults": {"cold": 99}},
        {"defaults": {"cold": True}},
        {"active": 99},
        {"active": True},
    ],
)
def test_inventory_rejects_inconsistent_state(fields):
    inventory, _ = add(Inventory())
    with pytest.raises(ValueError):
        dataclasses.replace(inventory, **fields)


def test_values_defend_nested_inputs_and_serialized_output():
    source = {"kind": "asset", "source_sha256": HASH, "nested": {"history": [HASH]}}
    inventory, _ = add(Inventory(), origin=source, palette=["#123456"], ranges=["cold"])
    source["nested"]["history"].append(OTHER)
    assert list(inventory.describe(1).origin["nested"]["history"]) == [HASH]
    with pytest.raises(TypeError):
        inventory.defaults["cold"] = 1
    with pytest.raises(TypeError):
        inventory.describe(1).origin["kind"] = "closet"
    dumped = inventory_to_dict(inventory)
    dumped["garments"][0]["origin"]["nested"]["history"].append(OTHER)
    assert list(inventory.describe(1).origin["nested"]["history"]) == [HASH]
    assert inventory_from_dict(inventory_to_dict(inventory)) == inventory


@pytest.mark.parametrize(
    "corrupt",
    [
        None,
        [],
        {},
        {
            "schema_version": 1,
            "next_number": 1,
            "garments": {},
            "retired": [],
            "defaults": {},
            "active": None,
        },
        {
            "schema_version": 1,
            "next_number": 1,
            "garments": [],
            "retired": [],
            "defaults": {},
            "active": None,
            "extra": True,
        },
    ],
)
def test_loading_rejects_malformed_shapes_as_value_errors(corrupt):
    with pytest.raises(ValueError):
        inventory_from_dict(corrupt)


def test_loading_rejects_missing_or_malformed_nested_fields():
    inventory, _ = add(Inventory())
    for key in ("asset", "origin", "number", "palette"):
        dumped = inventory_to_dict(inventory)
        del dumped["garments"][0][key]
        with pytest.raises(ValueError):
            inventory_from_dict(dumped)
    dumped = inventory_to_dict(inventory)
    dumped["garments"][0]["asset"]["files"] = "not-an-array"
    with pytest.raises(ValueError):
        inventory_from_dict(dumped)


def test_duplicate_identity_numbers_and_asset_roles_are_refused():
    inventory, garment = add(Inventory())
    with pytest.raises(ValueError):
        add(inventory)
    with pytest.raises(ValueError):
        dataclasses.replace(inventory, garments=(garment, garment))
    with pytest.raises(ValueError):
        AssetRef("anything", (asset().files[0], asset().files[0]))
    with pytest.raises(ValueError):
        add(Inventory(), version=2)


def test_pure_module_import_does_not_load_io_adapters_or_pillow():
    import subprocess
    import sys

    code = (
        "import sys; import mascot_wardrobe.domain.inventory; "
        "assert 'PIL' not in sys.modules; "
        "assert not any(m.startswith('mascot_wardrobe.infrastructure') for m in sys.modules)"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
