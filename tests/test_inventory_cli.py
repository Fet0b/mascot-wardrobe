"""Inventory CLI contracts, including inherited temporary data homes."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mascot_wardrobe.interfaces import cli


def test_cli_subprocess_inherits_an_isolated_data_home():
    home = Path(os.environ["MASCOT_WARDROBE_HOME"])
    assert home.is_absolute()
    assert not home.is_relative_to(Path(__file__).resolve().parents[1])
    child = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ['MASCOT_WARDROBE_HOME'])"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert child.stdout.strip() == str(home)


def test_inventory_parser_supports_lazy_data_home_and_named_range_alias():
    args = cli.parser().parse_args(["--data-dir", "some-home", "inventory", "list", "--range", "cold"])
    assert args.data_dir == Path("some-home")
    assert args.range == "cold"
    assert cli.parser().parse_args(["inventory", "list"]).data_dir is None
    args = cli.parser().parse_args(["inventory", "mudar-peca", "--country", "BR"])
    assert args.action == "mudar-peca"


def test_generation_opt_out_and_repeatable_closet_ranges_are_explicit():
    args = cli.parser().parse_args(
        ["generate", "--mascot", "m.png", "--id", "pet", "--country", "BR", "--no-inventory"]
    )
    assert args.no_inventory
    args = cli.parser().parse_args(
        ["closet", "add", "coat", "--template", "cold", "--range", "mild", "--range", "warm"]
    )
    assert args.ranges == ["mild", "warm"]


def test_change_requires_user_input_and_points_scripts_to_set_default(capsys):
    code = cli.main(["--no-input", "inventory", "change", "--country", "BR"], interactive=True)
    captured = capsys.readouterr()
    assert code == 2
    assert "set-default" in captured.err
    assert not Path(os.environ["MASCOT_WARDROBE_HOME"]).exists()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["inventory", "import-closet", "--root", "missing", "--replaces", "a" * 64], "--item"),
        (["inventory", "default", "--temperature", "12"], "--country"),
    ],
)
def test_conditional_arguments_fail_before_inventory_is_opened(arguments, message, capsys):
    assert cli.main(arguments) == 2
    assert message in capsys.readouterr().err
    assert not Path(os.environ["MASCOT_WARDROBE_HOME"]).exists()


def test_ranges_show_real_boundaries_without_opening_inventory(capsys):
    assert cli.main(["inventory", "ranges", "--country", "BR"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [row["range"] for row in rows] == list(cli.CATEGORIES)
    from mascot_wardrobe.infrastructure.policies import load_profile

    assert [row["max_c"] for row in rows[:-1]] == list(load_profile("BR").thresholds_c)
    assert not Path(os.environ["MASCOT_WARDROBE_HOME"]).exists()


def test_empty_inventory_is_listed_and_verified(capsys):
    assert cli.main(["inventory", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert cli.main(["inventory", "verify"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["garments"] == 0
    assert report["orphans"] == []


def test_application_inventory_exports_are_cycle_safe():
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "import mascot_wardrobe.application as public; "
            "from mascot_wardrobe.application import inventory; "
            "names = ('inventory_ranges', 'inventory_view', 'register_asset', 'register_manifest', "
            "'register_closet', 'register_closet_item', 'set_default', 'resolve_default', "
            "'select_from_inventory', 'closet_item_id_free', 'require_closet_item_id_free'); "
            "assert all(getattr(public, name) is getattr(inventory, name) for name in names)",
        ],
        capture_output=True,
        text=True,
    )
    assert child.returncode == 0, child.stderr


@pytest.fixture
def garment_files(tmp_path):
    from PIL import Image

    files = []
    for name, color in (("coat", "red"), ("shirt", "blue")):
        path = tmp_path / (name + ".png")
        Image.new("RGBA", (16, 16), color).save(path)
        files.append(path)
    return files


def import_garment(capsys, path, name="Coat", range_name="cold"):
    code = cli.main(["inventory", "import-asset", str(path), "--name", name, "--range", range_name])
    output = capsys.readouterr()
    assert code == 0, output.err
    return json.loads(output.out)


def test_asset_import_is_idempotent_and_list_includes_unsuitable_items(capsys, garment_files):
    assert import_garment(capsys, garment_files[0]) == {"number": 1, "status": "imported"}
    assert import_garment(capsys, garment_files[1], "Shirt", "warm")["number"] == 2
    assert import_garment(capsys, garment_files[0])["status"] == "unchanged"
    assert cli.main(["inventory", "list", "--range", "warm", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["number"] for r in rows] == [1, 2]
    assert [r["suitable"] for r in rows] == [False, True]


def test_default_and_selection_only_follow_user_mapping(capsys, garment_files):
    import_garment(capsys, garment_files[0])
    home = Path(os.environ["MASCOT_WARDROBE_HOME"]) / "inventory"

    def snapshot():
        return {str(p.relative_to(home)): p.read_bytes() for p in home.rglob("*") if p.is_file()}

    before = snapshot()
    assert cli.main(["inventory", "select", "--country", "BR", "--temperature", "28"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "no-default"
    assert snapshot() == before
    assert cli.main(["inventory", "default", "--range", "warm"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "no-default"
    assert snapshot() == before
    assert cli.main(["inventory", "set-default", "--range", "warm", "--number", "1"]) == 0
    capsys.readouterr()
    assert cli.main(["inventory", "default", "--range", "warm"]) == 0
    assert json.loads(capsys.readouterr().out)["number"] == 1
    assert cli.main(["inventory", "default", "--country", "BR", "--temperature", "82.4", "--unit", "F"]) == 0
    assert json.loads(capsys.readouterr().out)["number"] == 1
    assert cli.main(["inventory", "select", "--country", "BR", "--temperature", "28"]) == 0
    selected = json.loads(capsys.readouterr().out)
    assert selected["number"] == 1
    assert Path(selected["assets"]["portrait"]).is_absolute()
    assert json.loads((home / "inventory.json").read_text())["active"] == 1


def test_remove_retires_numbers_and_refuses_referenced_items(capsys, garment_files):
    import_garment(capsys, garment_files[0])
    assert cli.main(["inventory", "remove", "1"]) == 0
    capsys.readouterr()
    assert import_garment(capsys, garment_files[1], "Shirt")["number"] == 2
    assert cli.main(["inventory", "set-default", "--range", "cold", "--number", "2"]) == 0
    capsys.readouterr()
    path = Path(os.environ["MASCOT_WARDROBE_HOME"]) / "inventory/inventory.json"
    before = path.read_bytes()
    assert cli.main(["inventory", "remove", "2"]) == 2
    assert "cold" in capsys.readouterr().err
    assert cli.main(["inventory", "set-default", "--range", "cold", "--number", "1"]) == 2
    capsys.readouterr()
    assert path.read_bytes() == before


@pytest.mark.parametrize("action", ["change", "mudar-peca"])
def test_change_asks_named_range_shows_every_garment_and_saves_user_number(action, capsys, garment_files):
    import_garment(capsys, garment_files[0])
    import_garment(capsys, garment_files[1], "Shirt", "warm")
    answers = iter(["warm", "1"])
    assert (
        cli.main(
            ["inventory", action, "--country", "BR"], interactive=True, read=lambda prompt="": next(answers)
        )
        == 0
    )
    output = capsys.readouterr()
    assert "#1 Coat" in output.err
    assert "#2 Shirt" in output.err
    assert "cold" in output.err and "warm" in output.err
    assert "number" in output.err.lower()
    assert json.loads(output.out)["number"] == 1
    assert cli.main(["inventory", "default", "--range", "warm"]) == 0
    assert json.loads(capsys.readouterr().out)["number"] == 1


@pytest.mark.parametrize("answers", [["1", "invalid", "99"], ["warm", "9", "9", "9"], ["warm"]])
def test_change_invalid_or_eof_answers_preserve_inventory(answers, capsys, garment_files):
    import_garment(capsys, garment_files[0])
    path = Path(os.environ["MASCOT_WARDROBE_HOME"]) / "inventory/inventory.json"
    before = path.read_bytes()
    queue = iter(answers)

    def read(prompt=""):
        try:
            return next(queue)
        except StopIteration:
            raise EOFError from None

    assert cli.main(["inventory", "change", "--country", "BR"], interactive=True, read=read) == 2
    assert "error:" in capsys.readouterr().err
    assert path.read_bytes() == before


def test_change_empty_inventory_has_clear_error(capsys):
    assert (
        cli.main(["inventory", "change", "--country", "BR"], interactive=True, read=lambda prompt="": "cold")
        == 2
    )
    assert "empty" in capsys.readouterr().err.lower()


@pytest.fixture
def prepared_catalog(tmp_path):
    from mascot_wardrobe.application import prepare_wardrobe
    from tests.imaging import write_body_png

    source = tmp_path / "mascot.png"
    write_body_png(source, "#666666")
    manifest = prepare_wardrobe(source, "pet", "BR", tmp_path / "catalog")
    return source, manifest


def test_cli_manifest_import_preserves_catalog_and_is_idempotent(capsys, prepared_catalog):
    _, manifest = prepared_catalog
    before = {p.name: p.read_bytes() for p in manifest.parent.iterdir()}
    assert cli.main(["inventory", "import-manifest", str(manifest)]) == 0
    assert json.loads(capsys.readouterr().out) == {"imported": list(range(1, 7)), "skipped": []}
    assert cli.main(["inventory", "import-manifest", str(manifest)]) == 0
    assert json.loads(capsys.readouterr().out) == {"imported": [], "skipped": list(range(1, 7))}
    assert {p.name: p.read_bytes() for p in manifest.parent.iterdir()} == before


def test_human_inventory_list_keeps_all_numbers_and_suitability_hints(capsys, garment_files):
    import_garment(capsys, garment_files[0])
    import_garment(capsys, garment_files[1], "Shirt", "warm")
    assert cli.main(["inventory", "list", "--range", "warm"]) == 0
    text = capsys.readouterr().out
    assert "#1 Coat" in text and "#2 Shirt" in text
    assert "suitable: False" in text and "suitable: True" in text


def generate_arguments(source, output):
    return [
        "generate",
        "--mascot",
        str(source),
        "--id",
        "pet",
        "--country",
        "BR",
        "--output",
        str(output),
        "--style",
        "classic",
        "--palette",
        "auto",
    ]


def test_generate_registers_by_default_and_no_inventory_is_an_opt_out(capsys, prepared_catalog, tmp_path):
    source, _ = prepared_catalog
    arguments = generate_arguments(source, tmp_path / "generated")
    assert cli.main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["inventory"] == {"imported": list(range(1, 7)), "skipped": []}
    assert cli.main(arguments) == 0
    second = json.loads(capsys.readouterr().out)
    assert second.pop("inventory") == {"imported": [], "skipped": list(range(1, 7))}
    first.pop("inventory")
    assert first == second
    other_home = tmp_path / "opt-out-home"
    assert cli.main(["--data-dir", str(other_home), *arguments, "--no-inventory"]) == 0
    assert "inventory" not in json.loads(capsys.readouterr().out)
    assert not other_home.exists()


def test_generate_registration_failure_keeps_catalog_and_prints_recovery(
    capsys, prepared_catalog, tmp_path, monkeypatch
):
    from mascot_wardrobe.application import inventory

    source, _ = prepared_catalog
    output = tmp_path / "generated"

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(inventory, "register_manifest", fail)
    assert cli.main(generate_arguments(source, output)) == 2
    failure = capsys.readouterr()
    assert "inventory import-manifest" in failure.err
    assert "disk full" in failure.err
    assert (output / "manifest.json").is_file()
    assert len(list(output.glob("*.png"))) == 12


@pytest.fixture
def empty_closets(tmp_path):
    from mascot_wardrobe.closet import Closet
    from tests.test_closet import write_mascot, write_mask

    mascot, mask = tmp_path / "m.png", tmp_path / "mask.png"
    write_mascot(mascot)
    write_mask(mask)
    return [Closet.init(tmp_path / name, mascot, mask) for name in ("first", "second")]


def test_cli_closet_import_and_single_item_guarded_recovery(capsys, empty_closets):
    closet = empty_closets[0]
    item = closet.add_item("coat", "cold", ["#2878b5"], style="classic")
    arguments = ["inventory", "import-closet", "--root", str(closet.root)]
    assert cli.main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["imported"] == [1]
    closet.edit_item("coat", style="streetwear")
    assert cli.main(arguments) == 2
    assert "collision" in capsys.readouterr().err
    assert cli.main([*arguments, "--item", "coat", "--replaces", item["sha256"]]) == 0
    assert json.loads(capsys.readouterr().out)["updated"] == [1]


def test_closet_add_edit_register_and_preflight_cross_root_collision(capsys, empty_closets, monkeypatch):
    from mascot_wardrobe.closet import Closet
    from mascot_wardrobe.infrastructure.inventory_store import InventoryStore

    first, second = empty_closets
    addition = ["add", "coat", "--template", "cold", "--style", "classic", "--palette", "#2878b5"]
    assert cli.main(["closet", "--root", str(first.root), *addition, "--range", "mild"]) == 0
    added = json.loads(capsys.readouterr().out)
    assert added["inventory"] == {"number": 1, "status": "imported"}
    store = InventoryStore.open(Path(os.environ["MASCOT_WARDROBE_HOME"]))
    assert store.read().describe(1).ranges == ("cold", "mild")
    before = {str(p.relative_to(store.root)): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    second_before = {
        str(p.relative_to(second.root)): p.read_bytes() for p in second.root.rglob("*") if p.is_file()
    }

    def unexpected_render(*args, **kwargs):
        pytest.fail("collision must be refused before rendering")

    with monkeypatch.context() as patch:
        patch.setattr(Closet, "add_item", unexpected_render)
        assert cli.main(["closet", "--root", str(second.root), *addition]) == 2
    assert "collision" in capsys.readouterr().err
    assert {
        str(p.relative_to(second.root)): p.read_bytes() for p in second.root.rglob("*") if p.is_file()
    } == second_before
    assert {
        str(p.relative_to(store.root)): p.read_bytes() for p in store.root.rglob("*") if p.is_file()
    } == before
    assert cli.main(["closet", "--root", str(first.root), "edit", "coat", "--style", "streetwear"]) == 0
    edited = json.loads(capsys.readouterr().out)
    assert edited["inventory"] == {"number": 1, "status": "updated"}
    garment = store.read().describe(1)
    assert garment.version == 2
    assert garment.ranges == ("cold", "mild")
    assert garment.origin["history"] == (added["sha256"],)
    assert cli.main(["closet", "--root", str(first.root), "edit", "coat", "--name", "Parka"]) == 0
    capsys.readouterr()
    assert store.read().describe(1).name == "Parka"
    assert store.read().describe(1).version == 2


@pytest.mark.parametrize("failure_point", ["open", "register"])
def test_failed_edit_registration_keeps_saved_piece_and_gives_guarded_retry(
    failure_point,
    capsys,
    empty_closets,
    tmp_path,
    monkeypatch,
):
    import shlex

    from mascot_wardrobe.application import inventory
    from mascot_wardrobe.closet import Closet

    closet = empty_closets[0]
    home = tmp_path / "data with spaces"
    prefix = ["--data-dir", str(home), "closet", "--root", str(closet.root)]
    assert (
        cli.main([*prefix, "add", "coat", "--template", "cold", "--style", "classic", "--palette", "#2878b5"])
        == 0
    )
    previous = json.loads(capsys.readouterr().out)["sha256"]

    def fail(*args, **kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(
            cli if failure_point == "open" else inventory,
            "_inventory_store" if failure_point == "open" else "register_closet_item",
            fail,
        )
        assert cli.main([*prefix, "edit", "coat", "--style", "streetwear"]) == 2
    message = capsys.readouterr().err
    assert "was saved" in message
    assert previous in message
    assert "inventory import-closet" in message
    assert Closet.open(closet.root).items()["coat"]["style"] == "streetwear"
    retry = shlex.split(message.split("Retry: ", 1)[1].strip())
    assert retry[:3] == ["mascot-wardrobe", "--data-dir", str(home)]
    assert cli.main(retry[1:]) == 0
    assert json.loads(capsys.readouterr().out)["updated"] == [1]


def test_cli_piece_passes_inventory_store_and_persists_named_ranges(capsys, empty_closets):
    from mascot_wardrobe.infrastructure.inventory_store import InventoryStore
    from tests.test_piece import piece, script

    closet = empty_closets[0]
    read = script(*piece("cold,mild", "streetwear", "#2878b5"), "n", "n")
    assert (
        cli.main(
            ["closet", "--root", str(closet.root), "piece", "--country", "BR"], interactive=True, read=read
        )
        == 0
    )
    result = capsys.readouterr()
    made = json.loads(result.out)["pieces"][0]
    assert made["inventory"]["number"] == 1
    assert "inventory: #1" in result.err
    store = InventoryStore.open(Path(os.environ["MASCOT_WARDROBE_HOME"]))
    assert store.read().describe(1).ranges == ("cold", "mild")
    assert store.read().defaults == {}


def test_closet_range_only_edit_updates_suitability_without_rendering(capsys, empty_closets):
    from mascot_wardrobe.infrastructure.inventory_store import InventoryStore

    closet = empty_closets[0]
    prefix = ["closet", "--root", str(closet.root)]
    assert (
        cli.main([*prefix, "add", "coat", "--template", "cold", "--style", "classic", "--palette", "#2878b5"])
        == 0
    )
    first = json.loads(capsys.readouterr().out)
    assert cli.main([*prefix, "edit", "coat", "--range", "warm"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["version"], result["sha256"]) == (first["version"], first["sha256"])
    assert result["inventory"]["status"] == "updated"
    store = InventoryStore.open(Path(os.environ["MASCOT_WARDROBE_HOME"]))
    assert store.read().describe(1).ranges == ("cold", "warm")


@pytest.mark.parametrize("action", ["add", "edit"])
def test_printed_closet_retry_preserves_additional_ranges(action, capsys, empty_closets, monkeypatch):
    import shlex

    from mascot_wardrobe.application import inventory
    from mascot_wardrobe.infrastructure.inventory_store import InventoryStore, data_home

    closet = empty_closets[0]
    prefix = ["closet", "--root", str(closet.root)]
    if action == "edit":
        assert (
            cli.main(
                [*prefix, "add", "coat", "--template", "cold", "--style", "classic", "--palette", "#2878b5"]
            )
            == 0
        )
        capsys.readouterr()
        arguments = [*prefix, "edit", "coat", "--style", "streetwear", "--range", "mild"]
    else:
        arguments = [
            *prefix,
            "add",
            "coat",
            "--template",
            "cold",
            "--style",
            "classic",
            "--palette",
            "#2878b5",
            "--range",
            "mild",
        ]
    with monkeypatch.context() as patch:

        def fail(*args, **kwargs):
            raise OSError("disk full")

        patch.setattr(inventory, "register_closet_item", fail)
        assert cli.main(arguments) == 2
    message = capsys.readouterr().err
    retry = shlex.split(message.split("Retry: ", 1)[1].strip())
    assert cli.main(retry[1:]) == 0
    capsys.readouterr()
    garment = InventoryStore.open(data_home()).read().describe(1)
    assert garment.ranges == ("cold", "mild")
    assert garment.version == (2 if action == "edit" else 1)


def test_printed_piece_retry_preserves_named_ranges(capsys, empty_closets, monkeypatch):
    import shlex

    from mascot_wardrobe.application import inventory
    from mascot_wardrobe.infrastructure.inventory_store import InventoryStore, data_home
    from tests.test_piece import piece, script

    closet = empty_closets[0]
    read = script(*piece("cold,mild", "classic", "#2878b5"))
    with monkeypatch.context() as patch:

        def fail(*args, **kwargs):
            raise OSError("disk full")

        patch.setattr(inventory, "register_closet_item", fail)
        assert (
            cli.main(
                ["closet", "--root", str(closet.root), "piece", "--country", "BR"],
                interactive=True,
                read=read,
            )
            == 2
        )
    retry = shlex.split(capsys.readouterr().err.split("Retry: ", 1)[1].strip())
    assert retry[0].startswith("MASCOT_WARDROBE_HOME=")
    assert cli.main(retry[2:]) == 0
    capsys.readouterr()
    assert InventoryStore.open(data_home()).read().describe(1).ranges == ("cold", "mild")


@pytest.mark.parametrize("failure_point", ["open", "register"])
def test_failed_first_edit_has_executable_recovery(failure_point, capsys, empty_closets, monkeypatch):
    import shlex

    from mascot_wardrobe.application import inventory
    from mascot_wardrobe.infrastructure.inventory_store import InventoryStore, data_home

    closet = empty_closets[0]
    closet.add_item("legacy", "cold", ["#2878b5"], style="classic")
    with monkeypatch.context() as patch:

        def fail(*args, **kwargs):
            raise OSError("disk full")

        patch.setattr(
            cli if failure_point == "open" else inventory,
            "_inventory_store" if failure_point == "open" else "register_closet_item",
            fail,
        )
        assert (
            cli.main(
                [
                    "closet",
                    "--root",
                    str(closet.root),
                    "edit",
                    "legacy",
                    "--style",
                    "streetwear",
                    "--range",
                    "mild",
                ]
            )
            == 2
        )
    retry = shlex.split(capsys.readouterr().err.split("Retry: ", 1)[1].strip())
    assert cli.main(retry[1:]) == 0
    assert json.loads(capsys.readouterr().out)["imported"] == [1]
    garment = InventoryStore.open(data_home()).read().describe(1)
    assert (garment.style, garment.ranges, garment.version) == ("streetwear", ("cold", "mild"), 1)


@pytest.mark.parametrize("options", [[], ["--item", "coat"], ["--replaces", "a" * 64]])
def test_first_registration_recovery_flag_requires_item_and_guard(options, capsys):
    assert (
        cli.main(["inventory", "import-closet", "--root", "missing", "--allow-first-registration", *options])
        == 2
    )
    assert "--item" in capsys.readouterr().err


def test_closet_import_ranges_require_single_item(capsys):
    assert cli.main(["inventory", "import-closet", "--root", "missing", "--range", "cold"]) == 2
    assert "--range requires --item" in capsys.readouterr().err
