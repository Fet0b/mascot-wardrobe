"""Portable UTC date window for a caller-supplied badge; no personal artwork or dates."""

import json
from datetime import UTC, datetime
from unittest import mock

import pytest
from PIL import Image

from mascot_wardrobe.application import closet_weather
from tests import test_collections
from tests.test_collections import invoke

saved = test_collections.saved


@pytest.mark.parametrize(
    "day,enabled", [("2026-10-20", False), ("2026-10-21", True), ("2026-10-22", True), ("2026-10-23", False)]
)
def test_overlay_date_window_has_inclusive_utc_boundaries(day, enabled):
    now = datetime.fromisoformat(day).replace(tzinfo=UTC)
    chosen = closet_weather.overlay_for_date("badge.png", "2026-10-21", "2026-10-22", now=now)
    assert chosen == ("badge.png" if enabled else None)


@pytest.mark.parametrize(
    "overlay,start,end",
    [
        (None, "2026-10-21", "2026-10-22"),
        ("badge", None, "2026-10-22"),
        ("badge", "2026-10-23", "2026-10-22"),
        ("badge", "bad", "2026-10-22"),
    ],
)
def test_bad_overlay_windows_fail_closed(overlay, start, end):
    with pytest.raises(ValueError, match="overlay|Overlay"):
        closet_weather.overlay_for_date(overlay, start, end)


def test_undated_overlay_remains_opt_in():
    assert closet_weather.overlay_for_date(None, None, None) is None
    assert closet_weather.overlay_for_date("badge", None, None) == "badge"


def test_cli_maps_date_policy_to_profile_composition_without_filesystem_changes(saved):
    home, closet = saved
    from tests.test_closet_weather_publish import tree_state

    overlay = home.parent / "badge.png"
    Image.new("RGBA", (64, 64), "red").save(overlay)
    before = tree_state(home.parent)
    args = [
        "publish-closet",
        "--closet-root",
        closet.root,
        "--wardrobe",
        "looks",
        "--country",
        "BR",
        "--temperature",
        22,
    ]
    code, plain, err = invoke(*args)
    assert code == 0, err
    with mock.patch("mascot_wardrobe.application.closet_weather.datetime") as clock:
        clock.fromisoformat.side_effect = datetime.fromisoformat
        clock.now.return_value = datetime(2026, 10, 22, tzinfo=UTC)
        code, marked, err = invoke(
            *args,
            "--overlay",
            overlay,
            "--overlay-start-date",
            "2026-10-21",
            "--overlay-end-date",
            "2026-10-22",
        )
        assert code == 0, err
        clock.now.return_value = datetime(2026, 10, 23, tzinfo=UTC)
        code, expired, err = invoke(
            *args,
            "--overlay",
            overlay,
            "--overlay-start-date",
            "2026-10-21",
            "--overlay-end-date",
            "2026-10-22",
        )
        assert code == 0, err
    assert json.loads(plain)["jpg_sha256"] != json.loads(marked)["jpg_sha256"]
    assert json.loads(plain)["jpg_sha256"] == json.loads(expired)["jpg_sha256"]
    assert tree_state(home.parent) == before


@pytest.mark.parametrize(
    "stamp,enabled",
    [("2026-10-29T02:59:59+00:00", True), ("2026-10-29T03:00:00+00:00", False)],
)
def test_local_calendar_cutoff_uses_explicit_timezone(stamp, enabled):
    chosen = closet_weather.overlay_for_date(
        "badge",
        "0001-01-01",
        "2026-10-28",
        timezone="America/Sao_Paulo",
        now=datetime.fromisoformat(stamp),
    )
    assert chosen == ("badge" if enabled else None)


@pytest.mark.parametrize("zone,now", [("Invalid/Zone", datetime.now(UTC)), ("UTC", datetime(2026, 10, 22))])
def test_date_policy_rejects_unknown_zone_and_naive_time(zone, now):
    with pytest.raises(ValueError, match="timezone|Timezone"):
        closet_weather.overlay_for_date("badge", "2026-10-01", "2026-10-28", timezone=zone, now=now)


def test_enabled_overlay_changes_both_gateway_images_and_expiration_restores_them(saved):
    home, closet = saved
    overlay = home.parent / "badge.png"
    Image.new("RGBA", (64, 64), (255, 0, 160, 160)).save(overlay)
    args = (closet.root, "looks", "BR", 22)
    plain = closet_weather.publish_closet_look(*args, gateways=["hermes", "whatsapp"])
    marked = closet_weather.publish_closet_look(*args, overlay=overlay, gateways=["hermes", "whatsapp"])
    assert marked["jpg_sha256"] != plain["jpg_sha256"]
    assert marked["channels"]["hermes"]["atlas_sha256"] != plain["channels"]["hermes"]["atlas_sha256"]
    expired = closet_weather.overlay_for_date(
        overlay,
        "0001-01-01",
        "2026-10-28",
        timezone="America/Sao_Paulo",
        now=datetime.fromisoformat("2026-10-29T03:00:00+00:00"),
    )
    assert (
        closet_weather.publish_closet_look(*args, overlay=expired, gateways=["hermes", "whatsapp"]) == plain
    )


def test_cli_accepts_explicit_overlay_timezone(saved):
    home, closet = saved
    overlay = home.parent / "badge.png"
    Image.new("RGBA", (64, 64), "red").save(overlay)
    code, out, err = invoke(
        "publish-closet",
        "--closet-root",
        closet.root,
        "--wardrobe",
        "looks",
        "--country",
        "BR",
        "--temperature",
        22,
        "--gateway",
        "hermes",
        "--overlay",
        overlay,
        "--overlay-start-date",
        "0001-01-01",
        "--overlay-end-date",
        "9999-12-31",
        "--overlay-timezone",
        "America/Sao_Paulo",
    )
    assert code == 0, err
    assert json.loads(out)["overlay"] is True
