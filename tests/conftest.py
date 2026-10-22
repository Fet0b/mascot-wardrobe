"""Redirect inventory writes before collection and isolate independent CLI fixtures."""

import os
import tempfile

import pytest

_session_home = None
_previous_home = None


def pytest_configure(config):
    global _session_home, _previous_home
    _previous_home = os.environ.get("MASCOT_WARDROBE_HOME")
    _session_home = tempfile.TemporaryDirectory(prefix="wardrobe-tests-")
    os.environ["MASCOT_WARDROBE_HOME"] = _session_home.name


def pytest_unconfigure(config):
    if _previous_home is None:
        os.environ.pop("MASCOT_WARDROBE_HOME", None)
    else:
        os.environ["MASCOT_WARDROBE_HOME"] = _previous_home
    if _session_home is not None:
        _session_home.cleanup()


@pytest.fixture(autouse=True)
def inventory_data_home(tmp_path, monkeypatch):
    """Subprocesses inherit a fresh inventory, even when fixture mascot bytes repeat."""
    monkeypatch.setenv("MASCOT_WARDROBE_HOME", str(tmp_path / "inventory-home"))
