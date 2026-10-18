"""Docs-as-contract checks: hygiene, tables that mirror data, scheduler and traceability."""

import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".toml",
    ".json",
    ".yml",
    ".yaml",
    ".sh",
    ".svg",
    ".txt",
    ".example",
    ".baseline",
    "",
}
# Patterns are assembled from fragments so this file does not flag itself.
FORBIDDEN = {
    "root home": re.compile("/" + "root" + r"\b"),
    "user home": re.compile("/" + "home" + "/"),
    "mac home": re.compile("/" + "Users" + "/"),
    "former mascot name": re.compile("tobi" + "nha", re.IGNORECASE),
    "legacy dir": re.compile(r"\." + "her" + "mes" + r"\b|her" + "mes" + "/"),
    "ip address": re.compile(r"\b(?!127\.0\.0\.1\b|0\.0\.0\.0\b)\d{1,3}(?:\.\d{1,3}){3}\b"),
}


def _tracked_text_files():
    out = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for name in out:
        path = ROOT / name
        if path.resolve() == SELF or not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        yield name, path


def _profiles():
    path = ROOT / "src/mascot_wardrobe/infrastructure/profiles.json"
    return json.loads(path.read_text(encoding="utf-8"))["profiles"]


def _num(value):
    return f"{value:g}"


def test_tracked_text_has_no_personal_identifiers():
    hits = []
    for name, path in _tracked_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in FORBIDDEN.items():
            for match in pattern.finditer(text):
                hits.append(f"{name}: {label}: {match.group(0)}")
    assert not hits, hits


def test_readme_embeds_existing_relative_hero_and_key_sections():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"!\[[^\]]*\]\((assets/hero\.png)\)", readme)
    assert match, "README must embed assets/hero.png with a relative path"
    assert (ROOT / match.group(1)).is_file()
    assert "contact-sheet.png" in readme and (ROOT / "assets/contact-sheet.png").is_file()
    for needle in (
        "python -m venv .venv",
        "pip install .",
        "mascot-wardrobe generate",
        "mascot-wardrobe select",
        "mascot-wardrobe demo",
        "--live-weather",
        "--latitude",
        "Limitations",
        "Security",
    ):
        assert needle in readme, needle


def test_configuration_boundary_tables_match_profiles_json():
    doc = (ROOT / "docs/configuration.md").read_text(encoding="utf-8")
    categories = ("very_cold", "cold", "mild", "ambient", "warm", "very_hot")
    for code, profile in _profiles().items():
        start = doc.index(f"### {code}")
        nxt = doc.find("\n##", start + 1)
        section = doc[start : nxt if nxt != -1 else len(doc)]
        celsius = profile["thresholds_c"]
        fahrenheit = [c * 9 / 5 + 32 for c in celsius]
        rows = []
        for i, category in enumerate(categories):
            if i == 0:
                c_text, f_text = f"< {_num(celsius[0])}", f"< {_num(fahrenheit[0])}"
            elif i == len(categories) - 1:
                c_text, f_text = f">= {_num(celsius[-1])}", f">= {_num(fahrenheit[-1])}"
            else:
                c_text = f"{_num(celsius[i - 1])} to < {_num(celsius[i])}"
                f_text = f"{_num(fahrenheit[i - 1])} to < {_num(fahrenheit[i])}"
            rows.append(f"| {category} | {c_text} | {f_text} |")
        for row in rows:
            assert row in section, (code, row)


def test_configuration_states_rules_and_limits():
    doc = (ROOT / "docs/configuration.md").read_text(encoding="utf-8")
    for needle in (
        "upper category",
        "timezone=GMT",
        "utc_offset_seconds",
        "3 hours",
        "15 minutes",
        "Open-Meteo",
        "-90",
        "65",
    ):
        assert needle in doc, needle


def test_scheduler_examples_are_six_hourly_and_not_in_read_paths():
    folder = ROOT / "examples/scheduler"
    files = sorted(p.name for p in folder.iterdir())
    assert files == [
        "mascot-wardrobe.cron.example",
        "mascot-wardrobe.service.example",
        "mascot-wardrobe.timer.example",
    ]
    assert "*/6" in (folder / files[0]).read_text(encoding="utf-8")
    assert "00/6" in (folder / files[2]).read_text(encoding="utf-8")
    for name in files:
        assert name.endswith(".example")
    rel = folder.relative_to(ROOT).as_posix()
    assert not any(part in rel for part in ("/etc/", ".config/systemd", "cron.d"))
    doc = (ROOT / "docs/scheduler.md").read_text(encoding="utf-8")
    assert "never installed" in doc and "6 hours" in doc


def test_every_acceptance_criterion_is_in_traceability():
    spec = (ROOT / "docs/specs/public-release.md").read_text(encoding="utf-8")
    ids = sorted(set(re.findall(r"^- (AC-\d+)\b", spec, flags=re.MULTILINE)))
    assert len(ids) == 28
    table = (ROOT / "docs/traceability.md").read_text(encoding="utf-8")
    missing = [i for i in ids if not re.search(rf"^\| {i} \|", table, flags=re.MULTILINE)]
    assert not missing, missing


def test_traceability_references_resolve_to_real_tests_and_files():
    table = (ROOT / "docs/traceability.md").read_text(encoding="utf-8")
    refs = re.findall(r"`(tests/[a-z_]+\.py)::([A-Za-z0-9_]+)`", table)
    assert len(refs) >= 40
    for file, name in refs:
        source = (ROOT / file).read_text(encoding="utf-8")
        assert re.search(rf"def {name}\b", source), (file, name)
    for adr in set(re.findall(r"docs/adr/[0-9a-z-]+\.md", table)):
        assert (ROOT / adr).is_file(), adr


def test_adrs_and_project_documents_exist():
    for name in (
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CHANGELOG.md",
        "LICENSE",
        "docs/verification.md",
        "docs/wardrobe.md",
        "docs/adr/0001-layering.md",
        "docs/adr/0002-generate-once-no-inference.md",
        "docs/adr/0003-export-only-channels.md",
        "docs/adr/0004-weather-and-time-semantics.md",
    ):
        assert (ROOT / name).is_file(), name


def test_channel_docs_do_not_claim_the_bot_api_cannot_change_avatars():
    for name in ("README.md", "docs/channels.md", "docs/adr/0003-export-only-channels.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert not re.search(r"Bot API[^.\n]*(cannot|can't|impossible|does not support)", text), name
    channels = (ROOT / "docs/channels.md").read_text(encoding="utf-8")
    assert "not available in this project" in channels
    assert "X-Mascot-Wardrobe-Sandbox" in channels


def test_ci_workflow_is_pinned_least_privilege_and_uses_scripts():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in ci
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1" in ci
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0" in ci
    for version in ("3.11", "3.12", "3.13"):
        assert version in ci
    for uses in re.findall(r"uses: (\S+)", ci):
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", uses), uses
    assert "scripts/scan-secrets.sh" in ci and "scripts/e2e-smoke.sh" in ci
    assert "${{ secrets" not in ci


def test_scanner_gates_with_hook_and_scripts_are_executable():
    scan = (ROOT / "scripts/scan-secrets.sh").read_text(encoding="utf-8")
    assert "detect-secrets-hook" in scan and "--baseline" in scan
    assert "scan --baseline" not in scan
    smoke = (ROOT / "scripts/e2e-smoke.sh").read_text(encoding="utf-8")
    assert not re.search(r"\$cli[^\n]*--live-weather", smoke)
    for name in ("scripts/scan-secrets.sh", "scripts/e2e-smoke.sh"):
        assert (ROOT / name).stat().st_mode & 0o111, name


def test_detect_secrets_is_pinned_in_dev_extras_and_ci_and_build_outputs_are_ignored():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev = config["project"]["optional-dependencies"]["dev"]
    pin = next(d for d in dev if d.startswith("detect-secrets"))
    assert pin == "detect-secrets==1.5.0"
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "detect-secrets==1.5.0" in ci and "detect-secrets>=" not in ci
    assert "Python 3.12" in ci and "3.14" in ci and "untested" in ci
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "dist/" in ignored and "build/" in ignored


@pytest.mark.parametrize("name", ["architecture", "channels"])
def test_rewritten_docs_are_english(name):
    text = (ROOT / f"docs/{name}.md").read_text(encoding="utf-8")
    assert not re.search(r"[ãõçáéíóúâêô]", text)


def test_readme_network_and_reproducibility_statements_are_qualified():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "only external request" in readme and "loopback" in readme
    assert "only network call" not in readme
    assert "same Pillow" in readme


def test_architecture_describes_the_real_dependency_graph():
    arch = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
    adr = (ROOT / "docs/adr/0001-layering.md").read_text(encoding="utf-8")
    for text in (arch, adr):
        assert "inward" not in text.lower()
    assert "application imports" in arch and "domain imports nothing" in arch
    assert "supporting" in arch and "`Renderer`" in arch and "one real seam" in arch


def test_script_prerequisites_are_documented():
    for name in ("README.md", "CONTRIBUTING.md", "docs/verification.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "pip install build" in text, name
        assert ".venv/bin/python" in text, name
