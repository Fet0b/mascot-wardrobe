"""Docs-as-contract checks for the opt-in live adapters and the uninstalled publish scheduler example."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLISH = ROOT / "examples" / "publish"


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_publish_scheduler_examples_exist_and_are_never_installed():
    names = sorted(p.name for p in PUBLISH.iterdir())
    assert names == ["mascot-wardrobe-publish.service.example", "mascot-wardrobe-publish.timer.example"]
    service = (PUBLISH / names[0]).read_text(encoding="utf-8")
    timer = (PUBLISH / names[1]).read_text(encoding="utf-8")
    for text in (service, timer):
        assert "Never installed by mascot-wardrobe" in text
    assert "mascot-wardrobe publish" in service
    assert "--apply" in service and "--latitude" in service and "--live-weather" in service
    assert "EnvironmentFile=" in service
    assert "--whatsapp-bridge-url http://127.0.0.1:" in service
    assert re.search(r"\d{6,}:[A-Za-z0-9_-]{20,}", service) is None  # no token literal
    assert "OnCalendar" in timer and "Persistent=true" in timer
    assert not re.search(r"^\s*(sudo|systemctl)\b", service + timer, flags=re.MULTILINE)


def test_scheduler_doc_describes_publish_example_without_installing():
    doc = read("docs/scheduler.md")
    for needle in ("examples/publish/", "publish", "--apply", "systemctl --user disable", "No LLM"):
        assert needle in doc, needle


def test_channels_doc_describes_live_adapters_precisely():
    doc = read("docs/channels.md")
    for needle in (
        "dry-run",
        "--apply",
        "Hermes host",
        "approved photo directory",
        "bot itself",
        "personal",
        "acknowledged",
        "unverified",
        "MASCOT_WARDROBE_TELEGRAM_BOT_TOKEN",
        "rollback",
        "setMyProfilePhoto",
        "not available in this project",
        "X-Mascot-Wardrobe-Sandbox",
    ):
        assert needle in doc, needle
    assert "never marks Telegram as verified" in doc or "Telegram is never reported as verified" in doc


def test_adr_architecture_and_readme_describe_live_adapters():
    adr = read("docs/adr/0003-export-only-channels.md")
    assert "live adapters" in adr and "--apply" in adr and "Amended" in adr
    assert "sandbox" in adr and "Sandbox channel is preserved" in adr
    arch = read("docs/architecture.md")
    assert "channels/live.py" in arch and "publish_outfit" in arch
    readme = read("README.md")
    assert "mascot-wardrobe publish" in readme and "--apply" in readme and "dry-run" in readme
    assert "bot itself" in readme
    security = read("SECURITY.md")
    assert "live adapters" in security
    assert "publish" in read("CHANGELOG.md")


def test_integration_guide_states_the_hermes_boundary_and_uses_real_flags():
    guide = read("docs/integration.md")
    for needle in (
        "not** wired",
        "pip install -e '.[dev]'",
        "--apply",
        "dry-run",
        "--whatsapp-catalog-dir",
        "--telegram-expected-bot-id",
        "MASCOT_WARDROBE_TELEGRAM_BOT_TOKEN",
        "Rollback",
        "examples/publish/",
    ):
        assert needle in guide, needle
    assert "integration.md" in read("README.md")
