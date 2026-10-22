"""Active PetDex publishing: injected CLI and store, never the user's Hermes runtime."""

import json
import subprocess
from unittest import mock

import pytest

from mascot_wardrobe.channels.hermes_pet import HermesPetAdapter
from mascot_wardrobe.wardrobe.petdex import CODEX_STATE_ROWS, build_atlas
from tests.test_closet_weather_publish import tree_state
from tests.test_petdex import source_png


@pytest.fixture
def plan():
    return build_atlas(source_png())


class FakeHermes:
    def __init__(self, home):
        self.home = home
        self.active = "existing-avatar"
        self.calls = []
        self.fail_select = False
        self.bad_readback = False
        self.fail_rollback = False
        self.registered = []

    def run(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        command = argv[1:]
        if command == ["config", "path"]:
            output = str(self.home / "config.yaml")
        elif command == ["config", "get", "display.pet.slug"]:
            output = self.active
        elif command[:2] == ["pets", "select"]:
            slug = command[2]
            self.active = slug
            if self.fail_rollback and slug == "existing-avatar":
                raise OSError("rollback failed")
            if slug != "existing-avatar":
                if self.bad_readback:
                    self.active = "unexpected-avatar"
                if self.fail_select:
                    raise subprocess.TimeoutExpired(argv, 30)
            output = "selected"
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(argv, 0, output + "\n", "")

    def register(self, data, *, slug, display_name, description):
        self.registered.append(slug)
        folder = self.home / "pets" / slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "spritesheet.webp").write_bytes(data)
        (folder / "pet.json").write_text(json.dumps({"id": slug, "spritesheetPath": "spritesheet.webp"}))

    def adapter(self, **kwargs):
        return HermesPetAdapter(
            runner=self.run, register=self.register, state_rows=CODEX_STATE_ROWS, **kwargs
        )


@pytest.fixture
def runtime(tmp_path):
    home = tmp_path / "profiles" / "test-profile"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text("private sentinel; never parsed or written by package")
    fake = FakeHermes(home)
    fake.register(b"previous-sheet", slug=fake.active, display_name="old", description="old")
    fake.registered.clear()
    return fake


def test_preflight_does_not_write_and_apply_registers_selects_and_verifies(runtime, plan):
    before = tree_state(runtime.home)
    adapter = runtime.adapter()
    prepared = adapter.preflight(plan)
    assert tree_state(runtime.home) == before
    result = adapter.apply(plan, prepared=prepared)
    assert result["verified"] is True
    assert result["slug"] == plan.slug
    assert result["action"] == "selected"
    assert runtime.active == plan.slug
    assert runtime.registered == [plan.slug]
    assert (runtime.home / "pets" / plan.slug / "spritesheet.webp").read_bytes() == plan.png
    assert (runtime.home / "pets" / "existing-avatar" / "spritesheet.webp").read_bytes() == b"previous-sheet"
    assert (runtime.home / "config.yaml").read_text().startswith("private sentinel")
    assert [call[0][1:] for call in runtime.calls][-2:] == [
        ["pets", "select", plan.slug],
        ["config", "get", "display.pet.slug"],
    ]
    for _argv, options in runtime.calls[1:]:
        assert options["env"]["HERMES_HOME"] == str(runtime.home)
        assert options["check"] is True
        assert options["timeout"] == 30
        assert options.get("shell", False) is False


def test_identical_existing_atlas_is_reused_without_overwrite_and_repeat_is_unchanged(runtime, plan):
    adapter = runtime.adapter()
    adapter.apply(plan)
    runtime.registered.clear()
    before = tree_state(runtime.home)
    runtime.calls.clear()
    result = adapter.apply(plan)
    assert result["action"] == "unchanged"
    assert result["verified"]
    assert runtime.registered == []
    assert all("select" not in argv for argv, _ in runtime.calls)
    assert tree_state(runtime.home) == before


@pytest.mark.parametrize("collision", ["different", "incomplete", "symlink", "escaped-sheet"])
def test_occupied_slug_is_never_overwritten(runtime, plan, collision, tmp_path):
    folder = runtime.home / "pets" / plan.slug
    if collision == "symlink":
        folder.symlink_to(tmp_path)
    else:
        folder.mkdir()
        if collision != "incomplete":
            (folder / "spritesheet.webp").write_bytes(b"other")
            (folder / "pet.json").write_text(
                json.dumps(
                    {
                        "id": plan.slug,
                        "spritesheetPath": "../other" if collision == "escaped-sheet" else "spritesheet.webp",
                    }
                )
            )
    before = tree_state(runtime.home)
    with pytest.raises(ValueError, match="existing|symbolic links|unsafe|occupied"):
        runtime.adapter().apply(plan)
    assert runtime.active == "existing-avatar"
    assert not runtime.registered
    assert tree_state(runtime.home) == before


@pytest.mark.parametrize("failure", ["select", "readback", "rollback"])
def test_selection_failure_restores_previous_slug_with_cli_and_readback(runtime, plan, failure):
    runtime.fail_select = failure == "select"
    runtime.bad_readback = failure != "select"
    runtime.fail_rollback = failure == "rollback"
    with pytest.raises(RuntimeError, match="rollback failed" if failure == "rollback" else "restored"):
        runtime.adapter().apply(plan)
    assert (
        runtime.calls[-2][0][1:] == ["pets", "select", "existing-avatar"] if failure != "rollback" else True
    )
    assert runtime.active == "existing-avatar"
    if failure != "rollback":
        assert runtime.calls[-1][0][1:] == ["config", "get", "display.pet.slug"]


def test_registration_failure_never_selects(runtime, plan):
    adapter = runtime.adapter()
    adapter.register = mock.Mock(side_effect=RuntimeError("store failed"))
    with pytest.raises(RuntimeError, match="store failed"):
        adapter.apply(plan)
    assert runtime.active == "existing-avatar"
    assert all("select" not in argv for argv, _ in runtime.calls)


def test_taxonomy_drift_or_unusable_previous_slug_fails_before_any_write(runtime, plan):
    before = tree_state(runtime.home)
    adapter = runtime.adapter()
    adapter.state_rows = tuple(reversed(CODEX_STATE_ROWS))
    with pytest.raises(ValueError, match="taxonomy"):
        adapter.apply(plan)
    for invalid in ("", "null", "../bad", "no-such-pet"):
        runtime.active = invalid
        with pytest.raises(ValueError, match="previous|slug|installed"):
            runtime.adapter().apply(plan)
    assert not runtime.registered
    assert tree_state(runtime.home) == before


def test_race_occupying_slug_after_preflight_does_not_overwrite(runtime, plan):
    adapter = runtime.adapter()
    prepared = adapter.preflight(plan)
    folder = runtime.home / "pets" / plan.slug
    folder.mkdir()
    (folder / "sentinel").write_bytes(b"someone else's pet")
    before = tree_state(runtime.home)
    with pytest.raises((ValueError, FileExistsError), match="existing|occupied|File exists"):
        adapter.apply(plan, prepared=prepared)
    assert not runtime.registered
    assert tree_state(runtime.home) == before


def test_failed_cli_exit_return_is_rejected_even_with_an_injected_runner(runtime, plan):
    original = runtime.run

    def runner(argv, **kwargs):
        if argv[1:] == ["config", "get", "display.pet.slug"]:
            return subprocess.CompletedProcess(argv, 2, "existing-avatar\n", "private error")
        return original(argv, **kwargs)

    adapter = runtime.adapter()
    adapter.runner = runner
    before = tree_state(runtime.home)
    with pytest.raises(RuntimeError, match="failed") as error:
        adapter.apply(plan)
    assert "private error" not in str(error.value)
    assert not runtime.registered
    assert tree_state(runtime.home) == before


def test_runtime_rows_and_registration_scope_match_cli_profile(tmp_path, monkeypatch):
    import types

    from mascot_wardrobe.channels.hermes_pet import _runtime_store

    home = tmp_path / "profile"
    calls = []
    constants = types.ModuleType("hermes_constants")
    constants.set_hermes_home_override = lambda path: calls.append(("scope", path)) or "token"
    constants.reset_hermes_home_override = lambda token: calls.append(("reset", token))
    store = types.ModuleType("agent.pet.store")
    store.register_local_pet = lambda sheet, **kwargs: calls.append(("register", sheet, kwargs))
    pet_constants = types.ModuleType("agent.pet.constants")
    pet_constants.CODEX_STATE_ROWS = list(CODEX_STATE_ROWS)
    monkeypatch.setitem(__import__("sys").modules, "hermes_constants", constants)
    monkeypatch.setitem(__import__("sys").modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(__import__("sys").modules, "agent.pet", types.ModuleType("agent.pet"))
    monkeypatch.setitem(__import__("sys").modules, "agent.pet.store", store)
    monkeypatch.setitem(__import__("sys").modules, "agent.pet.constants", pet_constants)
    register, rows = _runtime_store(home)
    assert rows == CODEX_STATE_ROWS
    assert not calls
    register(b"sheet", slug="test")
    assert calls == [("scope", home), ("register", b"sheet", {"slug": "test"}), ("reset", "token")]


def test_reused_adapter_resolves_store_for_each_active_profile(runtime, plan, tmp_path):
    second = FakeHermes(tmp_path / "second-profile")
    second.home.mkdir()
    second.register(b"old", slug=second.active, display_name="old", description="old")
    second.registered.clear()
    adapter = HermesPetAdapter(runner=runtime.run)
    with mock.patch("mascot_wardrobe.channels.hermes_pet._runtime_store") as loader:
        loader.side_effect = [(runtime.register, CODEX_STATE_ROWS), (second.register, CODEX_STATE_ROWS)]
        adapter.apply(plan)
        adapter.runner = second.run
        adapter.apply(plan)
    assert [call.args[0] for call in loader.call_args_list] == [runtime.home, second.home]
    assert second.registered == [plan.slug]


def test_explicit_rollback_restores_and_verifies_preflight_selection(runtime, plan):
    adapter = runtime.adapter()
    prepared = adapter.preflight(plan)
    adapter.apply(plan, prepared=prepared)
    assert runtime.active == plan.slug
    result = adapter.rollback(prepared)
    assert result == {"verified": True, "slug": prepared.previous}
    assert runtime.active == prepared.previous


def test_explicit_rollback_failure_is_not_reported_verified(runtime, plan):
    adapter = runtime.adapter()
    prepared = adapter.preflight(plan)
    adapter.apply(plan, prepared=prepared)
    runtime.fail_rollback = True
    with pytest.raises(RuntimeError):
        adapter.rollback(prepared)
