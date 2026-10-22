"""Collection management and saved publisher routing use temporary data exclusively."""

import contextlib
import io
import json
import shutil
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest

from mascot_wardrobe.closet import Closet
from mascot_wardrobe.interfaces import cli
from tests.test_closet import write_mascot, write_mask
from tests.test_closet_weather_publish import tree_state


@pytest.fixture
def saved(tmp_path, monkeypatch):
    home = tmp_path / "data"
    monkeypatch.setenv("MASCOT_WARDROBE_HOME", str(home))
    write_mascot(tmp_path / "mascot.png")
    write_mask(tmp_path / "mask.png")
    closet = Closet.init(home / "closets" / "first", tmp_path / "mascot.png", tmp_path / "mask.png")
    from mascot_wardrobe.wardrobe import CATEGORIES

    for category in CATEGORIES:
        closet.add_item(category.replace("_", "-"), category)
    closet.save_wardrobe("looks", list(closet.items()))
    closet.save_wardrobe("current", list(closet.items()))
    closet.use_wardrobe("current")  # Only this temporary fixture is activated.
    (home / "inventory").mkdir()
    (home / "inventory" / "sentinel.json").write_text('{"untouched": true}')
    return home, closet


def invoke(*args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(map(str, args)), **kwargs)
        except SystemExit as error:
            code = error.code
    return code, out.getvalue(), err.getvalue()


def rows():
    code, out, err = invoke("collections", "list", "--json")
    assert code == 0, err
    return json.loads(out)


def selector(name="looks"):
    return next(row["id"] for row in rows() if row["wardrobe"] == name)


def choose(*gateways):
    flags = [part for gateway in gateways for part in ("--gateway", gateway)]
    code, out, err = invoke("collections", "select", selector(), *flags, interactive=False)
    assert code == 0, err
    return json.loads(out)


def state(home, *, old=False):
    path = home.parent / "weather.json"
    stamp = datetime.now(UTC) - (timedelta(days=1) if old else timedelta())
    path.write_text(json.dumps({"temperature_c": 22, "updated_at_utc": stamp.isoformat()}))
    return path


def run(home, *extra):
    return invoke("publish-closet", "--country", "BR", "--state-file", home.parent / "weather.json", *extra)


def test_list_discovers_nested_closets_with_stable_unambiguous_ids_and_no_writes(saved):
    home, closet = saved
    second = home / "nested" / "second"
    shutil.copytree(closet.root, second)
    before = tree_state(home)
    first = rows()
    assert len(first) == 4
    assert len({row["id"] for row in first}) == 4
    for row in first:
        assert set(row) == {"id", "wardrobe", "closet_root", "item_count", "active"}
        assert row["item_count"] == 6
        assert row["active"] == (row["wardrobe"] == "current")
    by_key = {(row["closet_root"], row["wardrobe"]): row["id"] for row in first}
    Closet.open(second).save_wardrobe("aaa", list(closet.items()))
    for row in rows():
        if row["wardrobe"] != "aaa":
            assert row["id"] == by_key[row["closet_root"], row["wardrobe"]]
    # The listing itself adds no files and does not change an existing manifest.
    assert tree_state(closet.root) == {
        k: v for k, v in before.items() if k.startswith(str(closet.root) + "/")
    }
    code, out, _ = invoke("collections", "list")
    assert code == 0
    for text in ("ID", "Wardrobe", "Closet root", "Items", "Active", "looks", str(closet.root)):
        assert text in out


def test_empty_default_home_list_is_read_only_and_data_dir_overrides_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("MASCOT_WARDROBE_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert rows() == []
    assert not (tmp_path / ".local").exists()
    other = tmp_path / "other"
    code, out, _ = invoke("--data-dir", other, "collections", "list", "--json")
    assert (code, json.loads(out)) == (0, [])
    assert not other.exists()


@pytest.fixture
def legacy_collections(saved):
    home, closet = saved
    for revision in (2, 3, 4):
        root = home / "closets" / f"legacy-t{revision}"
        shutil.copytree(closet.root, root)
        path = root / "closet.json"
        data = json.loads(path.read_text())
        data["renderer"] = {"renderer": "local-composer", "templates": revision}
        if revision == 2:
            for item in data["items"].values():
                for key in ("style", "style_choice", "palette_choice"):
                    item.pop(key, None)
        path.write_text(json.dumps(data))
    return home, closet


def test_list_all_four_current_and_legacy_closets_without_migration_or_writes(legacy_collections):
    home, _ = legacy_collections
    before = tree_state(home)
    with mock.patch.object(Closet, "migrate", side_effect=AssertionError("migration forbidden")):
        code, out, err = invoke("--data-dir", home, "collections", "list", "--json")
        assert code == 0, err
        listed = json.loads(out)
        assert len(listed) == 8
        assert len({row["closet_root"] for row in listed}) == 4
        assert len({row["id"] for row in listed}) == 8
        assert all(row["item_count"] == 6 for row in listed)
        assert all(row["active"] == (row["wardrobe"] == "current") for row in listed)
        assert invoke("--data-dir", home, "collections", "list")[0] == 0
    assert tree_state(home) == before


def test_remove_legacy_entry_retains_schema_assets_active_and_other_closets(legacy_collections):
    home, _ = legacy_collections
    # IDs have the same derivation for current and legacy manifests.
    from mascot_wardrobe.application.collections import _selector

    root = home / "closets" / "legacy-t2"
    chosen = _selector(home, root, "looks")
    before = tree_state(home)
    original = json.loads((root / "closet.json").read_text())
    with mock.patch.object(Closet, "migrate", side_effect=AssertionError("migration forbidden")):
        for flags in ((chosen,), (_selector(home, root, "current"), "--yes")):
            assert invoke("collections", "remove", *flags)[0] == 2
            assert tree_state(home) == before
        code, _, err = invoke("collections", "remove", chosen, "--yes")
        assert code == 0, err
    expected = json.loads(json.dumps(original))
    del expected["wardrobes"]["looks"]
    assert json.loads((root / "closet.json").read_text()) == expected
    after = tree_state(home)
    assert set(after) == set(before)
    assert {p for p in before if before[p] != after[p]} <= {str(root), str(root / "closet.json")}


def test_legacy_siblings_allow_current_selection_and_dry_run_without_closet_writes(legacy_collections):
    home, closet = legacy_collections
    before = tree_state(home)
    chosen = next(
        row["id"] for row in rows() if row["closet_root"] == str(closet.root) and row["wardrobe"] == "looks"
    )
    code, _, err = invoke("collections", "select", chosen, "--gateway", "hermes", "--gateway", "whatsapp")
    assert code == 0, err
    state(home)
    code, out, err = run(home)
    assert code == 0, err
    assert set(json.loads(out)["channels"]) == {"hermes", "whatsapp"}
    assert {k: v for k, v in tree_state(home).items() if k in before} == before
    assert set(tree_state(home)) - set(before) == {str(home / "weather-publisher.json")}


def test_select_writes_only_separate_atomic_publisher_selection(saved):
    home, closet = saved
    before = tree_state(home)
    result = choose("whatsapp", "hermes", "hermes")
    path = home / "weather-publisher.json"
    data = json.loads(path.read_text())
    assert data == {
        "schema_version": 1,
        "selector_id": selector(),
        "closet_root": str(closet.root),
        "wardrobe": "looks",
        "gateways": ["hermes", "whatsapp"],
    }
    assert result["selection_file"] == str(path)
    assert "active hermes petdex" in result["gateway_labels"]["hermes"].lower()
    assert "--apply" in result["gateway_labels"]["hermes"]
    assert {k: v for k, v in tree_state(home).items() if k in before} == before
    assert set(tree_state(home)) - set(before) == {str(path)}
    assert Closet.open(closet.root).active == "current"
    previous = path.read_bytes()
    with mock.patch("os.replace", side_effect=OSError("replace interrupted")):
        code, _, _ = invoke("collections", "select", selector(), "--gateway", "whatsapp")
    assert code == 2
    assert path.read_bytes() == previous
    assert set(tree_state(home)) - set(before) == {str(path)}


@pytest.mark.parametrize("flags", [(), ("--gateway", "telegram"), ("--gateway", "hermes-cli")])
def test_noninteractive_select_requires_supported_explicit_gateways(saved, flags):
    home, _ = saved
    before = tree_state(home)
    code, _, _ = invoke("collections", "select", selector(), *flags, interactive=False)
    assert code == 2
    assert tree_state(home) == before


def test_tty_select_prompts_for_collection_then_multiple_gateways(saved):
    home, _ = saved
    answers = iter([selector(), "hermes,whatsapp"])
    prompts = []

    def read(prompt):
        prompts.append(prompt)
        return next(answers)

    code, _, err = invoke("collections", "select", interactive=True, read=read)
    assert code == 0, err
    assert len(prompts) == 2
    assert "collection" in prompts[0].lower()
    assert "gateway" in prompts[1].lower()
    assert "active petdex pet/avatar" in prompts[1].lower()
    assert json.loads((home / "weather-publisher.json").read_text())["gateways"] == ["hermes", "whatsapp"]


def test_no_input_never_prompts_and_unknown_id_never_writes(saved):
    home, _ = saved
    before = tree_state(home)
    read = mock.Mock(side_effect=AssertionError("must not prompt"))
    for args in (("select",), ("select", selector()), ("select", "unknown", "--gateway", "hermes")):
        code, _, _ = invoke("--no-input", "collections", *args, interactive=True, read=read)
        assert code == 2
    assert tree_state(home) == before


def test_remove_requires_yes_refuses_active_and_retains_every_asset_and_inventory(saved):
    home, closet = saved
    chosen, active = selector(), selector("current")
    before = tree_state(home)
    for args in ((chosen,), (active, "--yes"), ("unknown", "--yes")):
        code, _, _ = invoke("collections", "remove", *args)
        assert code == 2
        assert tree_state(home) == before
    original = json.loads((closet.root / "closet.json").read_text())
    code, out, err = invoke("collections", "remove", chosen, "--yes")
    assert code == 0, err
    assert "retained" in out.lower()
    expected = json.loads(json.dumps(original))
    del expected["wardrobes"]["looks"]
    assert json.loads((closet.root / "closet.json").read_text()) == expected
    after = tree_state(home)
    assert set(after) == set(before)
    assert [p for p in before if before[p] != after[p] and p != str(closet.root)] == [
        str(closet.root / "closet.json")
    ]  # Atomic replacement also updates the manifest's parent directory mtime.


def test_saved_multi_gateway_dry_run_is_deterministic_no_writes_or_network(saved):
    home, _ = saved
    choose("hermes", "whatsapp")
    state(home)
    before = tree_state(home.parent)
    with (
        mock.patch("socket.socket.connect", side_effect=AssertionError("network forbidden")),
        mock.patch("tempfile.TemporaryDirectory", side_effect=AssertionError("staging forbidden")),
        mock.patch("pathlib.Path.write_bytes", side_effect=AssertionError("write forbidden")),
        mock.patch("pathlib.Path.write_text", side_effect=AssertionError("write forbidden")),
    ):
        first = run(home)
        second = run(home)
    assert first == second
    code, out, err = first
    assert code == 0, err
    result = json.loads(out)
    assert result["mode"] == "dry-run"
    assert result["channels"]["whatsapp"]["status"] == "planned"
    assert result["channels"]["hermes"]["target"] == "active-hermes-petdex"
    assert result["channels"]["hermes"]["remote_sync"] is False
    assert "active Hermes PetDex" in result["action"]
    assert tree_state(home.parent) == before


def test_apply_routes_each_selected_gateway_to_its_adapter(saved):
    home, closet = saved
    choose("hermes", "whatsapp")
    state(home)
    before = tree_state(closet.root)
    adapter = mock.Mock()
    adapter.sync.return_value = {"action": "updated", "verified": True}
    approved = home.parent / "approved"
    approved.mkdir()
    from mascot_wardrobe.application import WhatsAppTarget

    with (
        mock.patch(
            "mascot_wardrobe.interfaces.cli._whatsapp_target", return_value=WhatsAppTarget(approved, adapter)
        ),
        mock.patch("mascot_wardrobe.channels.hermes_pet.HermesPetAdapter") as factory,
    ):
        pet = factory.return_value
        pet.apply.return_value = {"action": "selected", "verified": True}
        code, out, err = run(home, "--apply")
    assert code == 0, err
    adapter.sync.assert_called_once()
    pet.preflight.assert_called_once()
    pet.apply.assert_called_once()
    plan = pet.preflight.call_args.args[0]
    assert pet.apply.call_args.args[0] == plan
    assert set(json.loads(out)["channels"]) == {"hermes", "whatsapp"}
    assert tree_state(closet.root) == before


def test_hermes_only_apply_selects_pet_and_repeat_is_unchanged_without_whatsapp(saved):
    home, closet = saved
    choose("hermes")
    state(home)
    before = tree_state(closet.root)
    from tests.test_hermes_pet import FakeHermes

    hermes_home = home.parent / "hermes-profile"
    hermes_home.mkdir()
    fake = FakeHermes(hermes_home)
    fake.register(b"old", slug=fake.active, display_name="old", description="old")
    fake.registered.clear()
    with (
        mock.patch("socket.socket.connect", side_effect=AssertionError("network forbidden")),
        mock.patch(
            "mascot_wardrobe.interfaces.cli._whatsapp_target",
            side_effect=AssertionError("WhatsApp forbidden"),
        ),
        mock.patch("mascot_wardrobe.channels.hermes_pet.HermesPetAdapter", return_value=fake.adapter()),
    ):
        first = run(home, "--apply")
        second = run(home, "--apply")
    assert first[0] == second[0] == 0, first[2]
    channel = json.loads(first[1])["channels"]["hermes"]
    assert channel["target"] == "active-hermes-petdex"
    assert channel["verified"] is True
    assert channel["action"] == "selected"
    assert json.loads(second[1])["channels"]["hermes"]["action"] == "unchanged"
    assert len(fake.registered) == 1
    assert fake.active == channel["slug"]
    assert tree_state(closet.root) == before


def test_gateway_failure_is_reported_and_other_selected_gateway_is_attempted(saved):
    home, _ = saved
    choose("hermes", "whatsapp")
    state(home)
    from mascot_wardrobe.application import WhatsAppTarget

    approved = home.parent / "approved"
    approved.mkdir()
    adapter = mock.Mock()
    adapter.sync.return_value = {"action": "updated", "verified": True}
    with (
        mock.patch(
            "mascot_wardrobe.interfaces.cli._whatsapp_target", return_value=WhatsAppTarget(approved, adapter)
        ),
        mock.patch("mascot_wardrobe.channels.hermes_pet.HermesPetAdapter") as factory,
    ):
        factory.return_value.apply.side_effect = RuntimeError("registration failed")
        code, out, _ = run(home, "--apply")
    assert code == 1
    result = json.loads(out)
    assert result["ok"] is False
    assert result["channels"]["hermes"]["status"] == "failed"
    assert result["channels"]["whatsapp"]["action"] == "updated"
    adapter.sync.assert_called_once()


def test_remove_uses_selector_owning_closet_with_duplicate_wardrobe_names(saved):
    home, closet = saved
    second = home / "second"
    shutil.copytree(closet.root, second)
    selected = next(
        row["id"] for row in rows() if row["closet_root"] == str(second) and row["wardrobe"] == "looks"
    )
    before = tree_state(closet.root)
    assert invoke("collections", "remove", selected, "--yes")[0] == 0
    assert "looks" not in Closet.open(second).wardrobes()
    assert tree_state(closet.root) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("gateways", []),
        ("gateways", None),
        ("gateways", [{}]),
        ("schema_version", True),
        ("schema_version", 2),
        ("wardrobe", None),
        ("wardrobe", "../looks"),
        ("closet_root", "relative"),
        ("closet_root", None),
        ("selector_id", "wrong"),
    ],
)
def test_malformed_saved_selection_fields_fail_closed(saved, field, value):
    home, _ = saved
    choose("hermes")
    state(home)
    path = home / "weather-publisher.json"
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    before = tree_state(home.parent)
    assert run(home, "--apply")[0] == 2
    assert tree_state(home.parent) == before


@pytest.mark.parametrize(
    "problem",
    [
        "missing-selection",
        "malformed",
        "gateways",
        "missing-wardrobe",
        "path",
        "missing-state",
        "stale-state",
        "stale-wardrobe",
        "missing-settings",
    ],
)
def test_saved_publish_fails_closed_before_any_apply_side_effect(saved, problem):
    home, closet = saved
    choose("hermes", "whatsapp")
    path = home / "weather-publisher.json"
    data = json.loads(path.read_text())
    state(home, old=problem == "stale-state")
    if problem == "missing-selection":
        path.unlink()
    elif problem == "malformed":
        path.write_text("[]")
    elif problem == "gateways":
        data["gateways"] = ["telegram"]
        path.write_text(json.dumps(data))
    elif problem == "missing-wardrobe":
        data["wardrobe"] = "gone"
        path.write_text(json.dumps(data))
    elif problem == "path":
        data["closet_root"] = str(home.parent / "outside")
        path.write_text(json.dumps(data))
    elif problem == "missing-state":
        (home.parent / "weather.json").unlink()
    elif problem == "stale-wardrobe":
        closet.edit_item("warm", style="streetwear")
    before = tree_state(home.parent)
    with mock.patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
        code, out, err = run(home, "--apply")
    assert (code, out) == (2, ""), err
    assert tree_state(home.parent) == before


def test_removed_selected_collection_fails_closed_and_weather_run_alias_works(saved):
    home, _ = saved
    choose("hermes")
    path = state(home)
    code, out, err = invoke("weather-run", "--country", "BR", "--state-file", path)
    assert code == 0, err
    assert json.loads(out)["channels"]["hermes"]["remote_sync"] is False
    code, _, err = invoke("collections", "remove", selector(), "--yes")
    assert code == 0, err
    before = tree_state(home.parent)
    assert run(home, "--apply")[0] == 2
    assert tree_state(home.parent) == before


def test_explicit_wardrobe_takes_precedence_and_partial_explicit_path_fails(saved):
    home, closet = saved
    choose("hermes")
    state(home)
    code, out, err = run(home, "--closet-root", closet.root, "--wardrobe", "looks")
    assert code == 0, err
    assert json.loads(out)["channels"] == {}
    for flags in (("--closet-root", closet.root), ("--wardrobe", "looks")):
        assert run(home, *flags)[0] == 2


def test_symlinked_closets_are_not_discovered_or_followed(saved):
    home, closet = saved
    (home / "alias").symlink_to(closet.root, target_is_directory=True)
    assert len(rows()) == 2
    (home / "weather-publisher.json").symlink_to(home.parent / "victim.json")
    assert invoke("collections", "select", selector(), "--gateway", "hermes")[0] == 2
    assert not (home.parent / "victim.json").exists()


@pytest.mark.parametrize("revision", [2, 3, 4])
def test_select_and_publish_legacy_collection_integrity_checked_without_migration(
    legacy_collections, revision
):
    home, _ = legacy_collections
    root = home / "closets" / f"legacy-t{revision}"
    from mascot_wardrobe.application.collections import _selector

    before = tree_state(root)
    inventory = tree_state(home / "inventory")
    with mock.patch.object(Closet, "migrate", side_effect=AssertionError("migration forbidden")):
        code, _, err = invoke(
            "collections", "select", _selector(home, root, "looks"), "--gateway", "whatsapp"
        )
        assert code == 0, err
        state(home)
        assert run(home)[0] == 0
        assert Closet.open(root, renderer_check=False).active == "current"
    assert tree_state(root) == before
    assert tree_state(home / "inventory") == inventory
    with pytest.raises(ValueError, match="template set"):
        Closet.open(root)
    # Skipping renderer compatibility never skips content or protected-pixel verification.
    item = root / json.loads((root / "closet.json").read_text())["items"]["ambient"]["file"]
    item.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="sha256"):
        Closet.open(root, renderer_check=False)


def test_explicit_repeated_gateways_and_whatsapp_only_skip_hermes_runtime(saved):
    home, closet = saved
    state(home)
    with mock.patch(
        "mascot_wardrobe.channels.hermes_pet.HermesPetAdapter", side_effect=AssertionError("live import")
    ):
        code, out, err = run(
            home,
            "--closet-root",
            closet.root,
            "--wardrobe",
            "looks",
            "--gateway",
            "hermes",
            "--gateway",
            "whatsapp",
            "--gateway",
            "hermes",
        )
        assert code == 0, err
        assert set(json.loads(out)["channels"]) == {"hermes", "whatsapp"}
        assert run(home, "--closet-root", closet.root, "--wardrobe", "looks", "--gateway", "whatsapp")[0] == 0


def test_hermes_preflight_failure_aborts_all_gateways_before_writes(saved):
    home, _ = saved
    choose("hermes", "whatsapp")
    state(home)
    approved = home.parent / "approved"
    approved.mkdir()
    from mascot_wardrobe.application import WhatsAppTarget

    adapter = mock.Mock()
    before = tree_state(home.parent)
    with (
        mock.patch(
            "mascot_wardrobe.interfaces.cli._whatsapp_target", return_value=WhatsAppTarget(approved, adapter)
        ),
        mock.patch("mascot_wardrobe.channels.hermes_pet.HermesPetAdapter") as factory,
    ):
        factory.return_value.preflight.side_effect = ValueError("occupied pet slug")
        assert run(home, "--apply")[0] == 2
    adapter.sync.assert_not_called()
    factory.return_value.apply.assert_not_called()
    assert tree_state(home.parent) == before


def test_bad_whatsapp_path_prevents_hermes_writes(saved):
    home, _ = saved
    choose("hermes", "whatsapp")
    state(home)
    from mascot_wardrobe.application import WhatsAppTarget

    before = tree_state(home.parent)
    adapter = mock.Mock()
    with (
        mock.patch(
            "mascot_wardrobe.interfaces.cli._whatsapp_target",
            return_value=WhatsAppTarget(home / "missing", adapter),
        ),
        mock.patch("mascot_wardrobe.channels.hermes_pet.HermesPetAdapter") as factory,
    ):
        code, _, err = run(home, "--apply")
        assert code == 2, err
    factory.return_value.apply.assert_not_called()
    adapter.sync.assert_not_called()
    assert tree_state(home.parent) == before


def test_isolated_dry_run_on_copy_of_saved_legacy_collection_has_no_effects(legacy_collections, monkeypatch):
    home, _ = legacy_collections
    copied = home.parent / "isolated" / "data"
    shutil.copytree(home, copied)
    monkeypatch.setenv("MASCOT_WARDROBE_HOME", str(copied))
    from mascot_wardrobe.application.collections import _selector

    root = copied / "closets" / "legacy-t2"
    code, _, err = invoke(
        "collections",
        "select",
        _selector(copied, root, "looks"),
        "--gateway",
        "hermes",
        "--gateway",
        "whatsapp",
    )
    assert code == 0, err
    weather = state(copied)
    before_original = tree_state(home)
    before_copy = tree_state(copied.parent)
    with (
        mock.patch("socket.socket", side_effect=AssertionError("network forbidden")),
        mock.patch("subprocess.run", side_effect=AssertionError("Hermes CLI forbidden")),
        mock.patch(
            "mascot_wardrobe.channels.hermes_pet._runtime_store",
            side_effect=AssertionError("store forbidden"),
        ),
        mock.patch(
            "mascot_wardrobe.channels.hermes_pet.HermesPetAdapter",
            side_effect=AssertionError("adapter forbidden"),
        ),
        mock.patch(
            "mascot_wardrobe.channels.live.HermesWhatsAppProfileAdapter._request",
            side_effect=AssertionError("bridge forbidden"),
        ),
        mock.patch("pathlib.Path.mkdir", side_effect=AssertionError("mkdir forbidden")),
        mock.patch("pathlib.Path.write_bytes", side_effect=AssertionError("write forbidden")),
        mock.patch("pathlib.Path.write_text", side_effect=AssertionError("write forbidden")),
        mock.patch("os.replace", side_effect=AssertionError("replace forbidden")),
        mock.patch("tempfile.TemporaryDirectory", side_effect=AssertionError("staging forbidden")),
        mock.patch.dict("sys.modules", {"agent.pet.store": None}),
    ):
        code, out, err = invoke("publish-closet", "--country", "BR", "--state-file", weather)
        assert code == 0, err
    result = json.loads(out)
    assert result["mode"] == "dry-run"
    assert result["weather"]["source"] == "state-file"
    assert result["channels"]["hermes"]["slug"].endswith(result["channels"]["hermes"]["atlas_sha256"])
    assert tree_state(copied.parent) == before_copy
    assert tree_state(home) == before_original


def test_legacy_read_only_open_still_rejects_protected_pixel_changes_with_updated_hash(legacy_collections):
    import hashlib

    from PIL import Image

    home, _ = legacy_collections
    root = home / "closets" / "legacy-t2"
    manifest = root / "closet.json"
    data = json.loads(manifest.read_text())
    record = data["items"]["ambient"]
    path = root / record["file"]
    with Image.open(path) as original:
        image = original.convert("RGBA")
    image.putpixel((0, 0), (255, 0, 0, 255))
    image.save(path)
    record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data))
    before = tree_state(root)
    with pytest.raises(ValueError, match="protected pixels"):
        Closet.open(root, renderer_check=False)
    assert tree_state(root) == before
