"""Portable mirror comparison reports paths only and never updates either tree."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_local_parity.py"


def compare(source, copy):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--source", str(source), "--copy", str(copy)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_manifest_reports_missing_extra_changed_relative_paths_without_mutation(tmp_path):
    source, copy = tmp_path / "source", tmp_path / "copy"
    for root in (source, copy):
        (root / "src").mkdir(parents=True)
        (root / "src" / "same.py").write_text("same")
        (root / "pyproject.toml").write_text("source" if root == source else "changed")
    (source / "src" / "missing.py").write_text("source only")
    (copy / "src" / "extra.py").write_text("copy only")
    from tests.test_closet_weather_publish import tree_state

    before = tree_state(tmp_path)
    result = compare(source, copy)
    assert result.returncode == 1, result.stderr
    assert json.loads(result.stdout) == {
        "ok": False,
        "missing": ["src/missing.py"],
        "extra": ["src/extra.py"],
        "changed": ["pyproject.toml"],
    }
    assert tree_state(tmp_path) == before


def test_explicit_private_generated_exclusions_keep_public_code_docs_and_images(tmp_path):
    source, copy = tmp_path / "source", tmp_path / "copy"
    source.mkdir()
    copy.mkdir()
    for name in (
        ".git",
        ".venv",
        ".aws",
        "state",
        "collections",
        "inventory",
        "pets",
        "audit",
        "sandbox",
        "node_modules",
        "dist",
        "src/__pycache__",
    ):
        folder = copy / name
        folder.mkdir(parents=True)
        (folder / "sentinel").write_text("private/generated; never reported")
    (copy / ".env").write_text("never read")
    (copy / ".env.local").write_text("never read")
    for root in (source, copy):
        (root / "docs").mkdir()
        (root / "docs" / "guide.md").write_text("public")
    result = compare(source, copy)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"]
    (copy / "docs" / "extra.png").write_bytes(b"public difference")
    result = compare(source, copy)
    assert json.loads(result.stdout)["extra"] == ["docs/extra.png"]


def test_missing_roots_or_included_symlinks_fail_closed_without_following(tmp_path):
    source, copy = tmp_path / "source", tmp_path / "copy"
    source.mkdir()
    assert compare(source, copy).returncode == 2
    copy.mkdir()
    (copy / "escape.py").symlink_to(source)
    result = compare(source, copy)
    assert result.returncode == 2
    assert "symlink" in result.stderr.lower()


def test_parity_document_has_behavior_map_and_private_runtime_boundary():
    doc = (ROOT / "docs" / "local-runtime-parity.md").read_text()
    for text in (
        "legacy/runtime/updater.py",
        "legacy/runtime/sync_avatar.py",
        "legacy/runtime/badge_policy.py",
        "legacy/assets/asset_builder.py",
        "HermesPetAdapter",
        "CODEX_STATE_ROWS",
        "Baileys",
        "--source",
        "--copy",
        "existing hourly timer",
        "updated_at_utc",
        "not verified",
        "private",
    ):
        assert text in doc


def test_closet_service_example_is_a_dry_run_and_does_not_install_a_schedule():
    text = (
        ROOT / "examples" / "closet-weather" / "mascot-wardrobe-closet-weather.service.example"
    ).read_text()
    command = text.split("ExecStart=", 1)[1]
    assert "--apply" not in command
    assert "--state-file" in command
    assert "--apply" in text  # Explicit opt-in explained separately.
    assert not list((ROOT / "examples" / "closet-weather").glob("*.timer*"))


def test_public_config_manifests_are_compared_even_when_named_config_yaml(tmp_path):
    source, copy = tmp_path / "source", tmp_path / "copy"
    for root in (source, copy):
        (root / "examples" / "config").mkdir(parents=True)
    (copy / "examples" / "config" / "config.yaml").write_text("public example")
    result = compare(source, copy)
    assert result.returncode == 1
    assert json.loads(result.stdout)["extra"] == ["examples/config/config.yaml"]
