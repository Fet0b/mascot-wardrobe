"""Preflight both selected targets before changing either profile, with fake runtimes only."""

import json
from unittest import mock

import pytest

from mascot_wardrobe.application import WhatsAppTarget, closet_weather
from mascot_wardrobe.channels.live import HermesWhatsAppProfileAdapter
from tests import test_collections
from tests.test_avatar_safety import MemoryBridge
from tests.test_closet_weather_publish import tree_state

saved = test_collections.saved


def targets(home, closet):
    bridge = MemoryBridge()
    bridge.slug = closet_weather.select_closet_look(closet.root, "looks", "BR", 22)["slug"]
    catalog = home.parent / "approved"
    catalog.mkdir()
    adapter = HermesWhatsAppProfileAdapter(
        "http://127.0.0.1:1",
        bridge_header="X-Example-Avatar: 1",
        opener=bridge,
        state_dir=home.parent / "sync-state",
        pause=lambda seconds: None,
    )
    pet = mock.Mock()
    pet.apply.return_value = {"action": "selected", "verified": True}
    pet.rollback.return_value = {"verified": True, "slug": "previous"}
    return bridge, WhatsAppTarget(catalog, adapter), pet


def test_unreachable_whatsapp_aborts_before_hermes_changes(saved):
    home, closet = saved
    bridge, whatsapp, pet = targets(home, closet)
    bridge.failure = True
    before = tree_state(closet.root)
    with pytest.raises(RuntimeError, match="Bridge GET failed"):
        closet_weather.publish_closet_look(
            closet.root, "looks", "BR", 22, apply=True, whatsapp=whatsapp, hermes=pet
        )
    pet.preflight.assert_called_once()
    pet.apply.assert_not_called()
    assert bridge.posts == 0
    assert tree_state(closet.root) == before


def test_both_preflights_precede_profile_changes_and_lock_covers_hermes_delivery(saved):
    home, closet = saved
    bridge, whatsapp, pet = targets(home, closet)

    def apply(*args, **kwargs):
        assert bridge.events == ["GET"]
        with pytest.raises(BlockingIOError):
            whatsapp.adapter.sync(bridge.slug)
        return {"action": "selected", "verified": True}

    pet.apply.side_effect = apply
    result = closet_weather.publish_closet_look(
        closet.root, "looks", "BR", 22, apply=True, whatsapp=whatsapp, hermes=pet
    )
    assert result["ok"]
    assert bridge.events == ["GET", "GET", "POST", "GET"]
    state = whatsapp.adapter.state_dir / "avatar-sync.json"
    assert json.loads(state.read_text())["verified"]


def test_whatsapp_failure_rolls_back_hermes_and_reports_actual_outcomes(saved):
    home, closet = saved
    bridge, whatsapp, pet = targets(home, closet)
    bridge.match_after = 99
    result = closet_weather.publish_closet_look(
        closet.root, "looks", "BR", 22, apply=True, whatsapp=whatsapp, hermes=pet
    )
    assert not result["ok"]
    assert result["channels"]["whatsapp"]["status"] == "failed"
    assert result["channels"]["hermes"]["status"] == "rolled-back"
    pet.rollback.assert_called_once_with(pet.preflight.return_value)
    assert not (whatsapp.adapter.state_dir / "avatar-sync.json").exists()


def test_cross_gateway_rollback_failure_is_explicit(saved):
    home, closet = saved
    bridge, whatsapp, pet = targets(home, closet)
    bridge.match_after = 99
    pet.rollback.side_effect = RuntimeError("synthetic rollback failure")
    result = closet_weather.publish_closet_look(
        closet.root, "looks", "BR", 22, apply=True, whatsapp=whatsapp, hermes=pet
    )
    assert not result["ok"]
    assert result["channels"]["hermes"]["status"] == "rollback-failed"
    assert "synthetic rollback failure" in result["channels"]["hermes"]["error"]
