"""Saved user mappings alone determine inventory temperature selection."""

import pytest

from mascot_wardrobe.domain.models import Category
from mascot_wardrobe.infrastructure.policies import load_profile


def test_inventory_ranges_uses_named_categories_and_profile_boundaries():
    from mascot_wardrobe.application.inventory import inventory_ranges

    for country in ("BR", "US", "GB", "JP"):
        profile = load_profile(country)
        rows = inventory_ranges(country)
        assert [row["range"] for row in rows] == [c.value for c in Category]
        assert [row["min_c"] for row in rows] == [None, *profile.thresholds_c]
        assert [row["max_c"] for row in rows] == [*profile.thresholds_c, None]


def test_view_defaults_and_selection_preserve_unsuitable_user_choice(tmp_path):
    from mascot_wardrobe.application.inventory import (
        inventory_view,
        register_asset,
        resolve_default,
        select_from_inventory,
        set_default,
    )
    from tests.test_inventory_import import png, store_at

    store = store_at(tmp_path)
    register_asset(png(tmp_path / "coat.png"), store, name="Coat", ranges=("cold",))
    register_asset(png(tmp_path / "shirt.png", "blue"), store, name="Shirt", ranges=("very_hot",))
    rows = inventory_view(store, range="very_hot")
    assert [row["number"] for row in rows] == [1, 2]
    assert [row["suitable"] for row in rows] == [False, True]
    assert {"name", "style", "palette", "ranges", "format", "version"} <= rows[0].keys()
    set_default(store, "very_hot", 1)
    selected = select_from_inventory(store, "BR", 40)
    assert selected["number"] == 1
    assert selected["category"] == "very_hot"
    assert selected["status"] == "selected"
    assert selected["country_code"] == "BR"
    assert selected["assets"]["portrait"] == str(store.asset_path(store.read().describe(1).asset.files[0]))
    assert store.read().active == 1
    assert resolve_default(store, "BR", 104, "F")["number"] == 1


@pytest.mark.parametrize("country", ["BR", "US", "GB", "JP"])
def test_thresholds_and_no_default_never_mutate_inventory(tmp_path, country):
    from mascot_wardrobe.application.inventory import resolve_default, select_from_inventory
    from tests.test_inventory_import import store_at

    store = store_at(tmp_path)
    before = (store.root / "inventory.json").read_bytes()
    files = sorted(str(p.relative_to(store.root)) for p in store.root.rglob("*") if p.is_file())
    profile = load_profile(country)
    for index, threshold in enumerate(profile.thresholds_c):
        result = select_from_inventory(store, country, threshold)
        assert result == {
            "status": "no-default",
            "category": tuple(Category)[index + 1].value,
            "temperature_c": float(threshold),
        }
        assert resolve_default(store, country, threshold) == result
    assert (store.root / "inventory.json").read_bytes() == before
    assert sorted(str(p.relative_to(store.root)) for p in store.root.rglob("*") if p.is_file()) == files
    assert store.read().active is None


@pytest.mark.parametrize("temperature", [float("nan"), float("inf"), -91, 66])
def test_invalid_temperature_keeps_legacy_validation(tmp_path, temperature):
    from mascot_wardrobe.application.inventory import select_from_inventory
    from tests.test_inventory_import import store_at

    with pytest.raises(ValueError, match="finite"):
        select_from_inventory(store_at(tmp_path), "BR", temperature)


def test_remove_refuses_default_and_active_then_retires_number(tmp_path):
    from mascot_wardrobe.application.inventory import register_asset, remove_garment, set_default
    from tests.test_inventory_import import png, store_at

    store = store_at(tmp_path)
    register_asset(png(tmp_path / "coat.png"), store, name="Coat", ranges=("cold",))
    set_default(store, "warm", 1)
    with pytest.raises(ValueError, match="warm"):
        remove_garment(store, 1)
    register_asset(png(tmp_path / "shirt.png", "blue"), store, name="Shirt", ranges=("cold",))
    remove_garment(store, 2)
    assert store.read().retired == (2,)
    register_asset(png(tmp_path / "other.png", "green"), store, name="Other", ranges=("cold",))
    assert [g.number for g in store.read().garments] == [1, 3]


@pytest.mark.parametrize("country", ["BR", "US", "GB", "JP"])
def test_missing_default_preserves_active_and_ignores_neighbor_mappings(tmp_path, country):
    from mascot_wardrobe.application.inventory import (
        register_asset,
        resolve_default,
        select_from_inventory,
        set_default,
    )
    from tests.test_inventory_import import png, store_at

    store = store_at(tmp_path)
    register_asset(png(tmp_path / "coat.png"), store, name="Coat", ranges=("ambient", "mild"))
    register_asset(png(tmp_path / "shirt.png", "blue"), store, name="Shirt", ranges=("warm",))
    set_default(store, "mild", 1)
    set_default(store, "warm", 2)
    thresholds = load_profile(country).thresholds_c
    assert select_from_inventory(store, country, (thresholds[3] + thresholds[4]) / 2)["number"] == 2

    def snapshot():
        return {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}

    before = snapshot()
    temperature = (thresholds[2] + thresholds[3]) / 2
    expected = {"status": "no-default", "category": "ambient", "temperature_c": temperature}
    assert resolve_default(store, country, temperature) == expected
    assert snapshot() == before
    assert select_from_inventory(store, country, temperature) == expected
    assert snapshot() == before
    assert store.read().active == 2
    assert dict(store.read().defaults) == {"mild": 1, "warm": 2}
