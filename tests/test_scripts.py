"""Safety and behaviour of the helper scripts, exercised without touching the real repo evidence."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
E2E = ROOT / "scripts" / "e2e-smoke.sh"
SCAN = ROOT / "scripts" / "scan-secrets.sh"


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHON", "MASCOT_WARDROBE_EVIDENCE_DIR"}}
    env.update(extra)
    return env


def _run(script: Path, cwd: Path, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script)], cwd=cwd, env=_env(**env), capture_output=True, text=True, check=False
    )


def _fake_python(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _resolved(result: subprocess.CompletedProcess) -> str:
    assert result.returncode == 0, result.stderr
    line = next(line for line in result.stdout.splitlines() if line.startswith("python: "))
    return line.removeprefix("python: ")


def test_e2e_python_prefers_explicit_then_repo_venv_then_python3(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    evidence = tmp_path / "evidence"
    common = {"E2E_RESOLVE_ONLY": "1", "MASCOT_WARDROBE_EVIDENCE_DIR": str(evidence)}
    assert _resolved(_run(E2E, repo, **common)) == "python3"
    venv_python = _fake_python(repo / ".venv" / "bin" / "python")
    assert _resolved(_run(E2E, repo, **common)) == str(venv_python)
    explicit = _fake_python(tmp_path / "other" / "python")
    assert _resolved(_run(E2E, repo, PYTHON=str(explicit), **common)) == str(explicit)


def test_e2e_fails_with_a_clear_message_when_build_is_missing(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    stub = tmp_path / "python"
    stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")  # every "-c import build" fails
    stub.chmod(0o755)
    result = _run(E2E, repo, PYTHON=str(stub), MASCOT_WARDROBE_EVIDENCE_DIR=str(tmp_path / "evidence"))
    assert result.returncode != 0
    assert "build" in result.stderr and "pip install build" in result.stderr


def test_e2e_never_deletes_existing_evidence_content(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    evidence = tmp_path / "evidence"
    (evidence / "work").mkdir(parents=True)
    sentinels = [evidence / "keep.txt", evidence / "work" / "keep.txt", evidence / "work" / "mascot.png"]
    for sentinel in sentinels:
        sentinel.write_text("sentinel", encoding="utf-8")
    env = {"E2E_ALLOCATE_ONLY": "1", "MASCOT_WARDROBE_EVIDENCE_DIR": str(evidence)}
    fake = _fake_python(tmp_path / "python")
    first = _run(E2E, repo, PYTHON=str(fake), **env)
    second = _run(E2E, repo, PYTHON=str(fake), **env)
    assert first.returncode == 0 and second.returncode == 0, first.stderr + second.stderr
    assert all(s.read_text(encoding="utf-8") == "sentinel" for s in sentinels)
    runs = {line for line in (first.stdout + second.stdout).splitlines() if line.startswith("run dir: ")}
    assert len(runs) == 2, "each run must allocate its own fresh directory"


def test_e2e_rejects_an_evidence_dir_inside_the_repository(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    result = _run(
        E2E,
        repo,
        PYTHON=str(_fake_python(tmp_path / "python")),
        E2E_ALLOCATE_ONLY="1",
        MASCOT_WARDROBE_EVIDENCE_DIR=str(repo / "ev"),
    )
    assert result.returncode != 0 and "outside the repository" in result.stderr


def test_script_files_are_executable_bash():
    for script in (E2E, SCAN):
        assert script.stat().st_mode & 0o111


def _scan_repo(path: Path) -> Path:
    """A throwaway git repo with an empty detect-secrets baseline."""
    tools = ROOT / ".venv" / "bin"
    if not (tools / "detect-secrets").exists() or not (tools / "detect-secrets-hook").exists():
        pytest.skip("needs the repo .venv with detect-secrets (dev checkout only, not an extracted sdist)")
    repo = _git_repo(path)
    baseline = subprocess.run(
        [str(tools / "detect-secrets"), "scan"], cwd=repo, capture_output=True, text=True, check=True
    )
    (repo / ".secrets.baseline").write_text(baseline.stdout, encoding="utf-8")
    (repo / "README.txt").write_text("nothing to see\n", encoding="utf-8")
    return repo


def test_scan_report_passes_on_a_clean_tree_and_lists_only_counts(tmp_path):
    repo = _scan_repo(tmp_path / "repo")
    report = tmp_path / "report.txt"
    result = subprocess.run(
        ["bash", str(SCAN), str(report)],
        cwd=ROOT,
        env=_env(SCAN_ROOT=str(repo)),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    text = report.read_text(encoding="utf-8")
    assert "status: passed" in text and "exit code: 0" in text and "findings: 0" in text


def test_scan_report_for_a_planted_placeholder_has_location_but_never_the_value(tmp_path):
    repo = _scan_repo(tmp_path / "repo")
    # Benign placeholder assembled at runtime so this file itself holds no secret-looking literal.
    key = "".join(["pass", "word"])
    value = "".join(["Zq8x", "K2mV", "9rT4", "wL7p"])
    (repo / "settings.txt").write_text(f'{key} = "{value}"\n', encoding="utf-8")
    report = tmp_path / "report.txt"
    result = subprocess.run(
        ["bash", str(SCAN), str(report)],
        cwd=ROOT,
        env=_env(SCAN_ROOT=str(repo)),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    text = report.read_text(encoding="utf-8")
    assert "status: failed" in text and "findings: 1" in text
    assert "settings.txt:1" in text and "Secret Keyword" in text
    assert value not in text and key not in text
    assert all(len(line) < 120 for line in text.splitlines())
