"""The source distribution is complete and clean, and the wheel stays runtime-only."""

import fnmatch
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

REQUIRED = [
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "pyproject.toml",
    "MANIFEST.in",
    ".secrets.baseline",
    ".github/workflows/ci.yml",
    "art/generate_art.py",
    "art/mascot.svg",
    "art/README.md",
    "assets/hero.png",
    "assets/contact-sheet.png",
    "examples/mascot.png",
    "examples/hermes-skill/mudar-peca/SKILL.md",
    "examples/scheduler/mascot-wardrobe.cron.example",
    "examples/scheduler/mascot-wardrobe.service.example",
    "examples/scheduler/mascot-wardrobe.timer.example",
    "docs/architecture.md",
    "docs/adr/0001-layering.md",
    "docs/specs/public-release.md",
    "scripts/e2e-smoke.sh",
    "scripts/scan-secrets.sh",
    "plans/2026-10-18_public-release.md",
    "tests/test_sdist.py",
    "tests/test_cli.py",
    "src/mascot_wardrobe/__init__.py",
    "src/mascot_wardrobe/interfaces/cli.py",
    "src/mascot_wardrobe/infrastructure/profiles.json",
]

FORBIDDEN_PARTS = {
    ".git",
    ".venv",
    ".claude",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "sandbox",
    "state",
    "logs",
    "audit",
    "output",
    "mascot-wardrobe-out",
}
FORBIDDEN_GLOBS = [".env", ".env.*", "*.log", "*.lock", "*.pyc", "*.egg-info"]


def _copy_tree(dest: Path) -> Path:
    """Copy the working tree (without .git/.venv) so the build never writes into the repo."""
    skip = shutil.ignore_patterns(".git", ".venv")
    shutil.copytree(ROOT, dest, ignore=skip)
    return dest


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    base = tmp_path_factory.mktemp("sdist")
    src = _copy_tree(base / "src")
    out = base / "out"
    result = subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--outdir", str(out), str(src)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    (archive,) = out.glob("*.tar.gz")
    with tarfile.open(archive) as tar:
        prefix = archive.name.removesuffix(".tar.gz")
        names = [m.name.removeprefix(prefix + "/") for m in tar.getmembers() if m.name != prefix]
        readme = tar.extractfile(f"{prefix}/README.md").read().decode("utf-8")
    return {"names": names, "readme": readme, "out": out, "archive": archive, "src": src}


def test_sdist_contains_all_public_paths(built):
    names = set(built["names"])
    missing = [p for p in REQUIRED if p not in names]
    assert not missing, missing
    for prefix in ("docs/", "scripts/", "plans/", "tests/", "art/", "examples/scheduler/"):
        assert any(n.startswith(prefix) for n in names), prefix


def test_sdist_has_no_forbidden_content(built):
    bad = []
    for name in built["names"]:
        parts = name.split("/")
        if name.endswith(".egg-info") or ".egg-info/" in name:
            # setuptools writes the PKG-INFO metadata directory into every sdist
            if parts[0] == "src" and len(parts) > 1 and parts[1] == "mascot_wardrobe.egg-info":
                continue
        if FORBIDDEN_PARTS.intersection(parts) or any(
            fnmatch.fnmatch(part, g) for part in parts for g in FORBIDDEN_GLOBS
        ):
            bad.append(name)
    assert not bad, bad


def test_sdist_readme_relative_links_exist_in_sdist(built):
    names = set(built["names"])
    dirs = {str(Path(n).parent) for n in names} | {n.rstrip("/") for n in names}
    links = re.findall(r"\]\(([^)\s]+)\)", built["readme"])
    local = [link.split("#")[0] for link in links if not re.match(r"[a-z]+:", link) and link[0] != "#"]
    assert local
    missing = [link for link in local if link.rstrip("/") not in names and link.rstrip("/") not in dirs]
    assert not missing, missing


def test_wheel_from_sdist_is_runtime_only(built, tmp_path):
    with tarfile.open(built["archive"]) as tar:
        tar.extractall(tmp_path / "unpacked", filter="data")
    (unpacked,) = (tmp_path / "unpacked").iterdir()
    wheel_dir = tmp_path / "wheel"
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(wheel_dir), str(unpacked)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    (wheel,) = wheel_dir.glob("*.whl")
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    assert "mascot_wardrobe/infrastructure/profiles.json" in names
    assert any(n.endswith(".dist-info/licenses/LICENSE") for n in names)
    stray = [n for n in names if not n.startswith("mascot_wardrobe/") and ".dist-info/" not in n]
    assert not stray, stray
    assert not [n for n in names if re.match(r"(art|assets|docs|scripts|examples|tests|plans)/", n)]
