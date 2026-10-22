"""Publish one saved closet look chosen by the country profile's weather category.

Read-only on the closet: no regeneration, no inventory write, no change of the active wardrobe.
"""

import hashlib
import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from mascot_wardrobe.closet import Closet
from mascot_wardrobe.domain.models import to_celsius, validate_temperature
from mascot_wardrobe.infrastructure.policies import load_profile
from mascot_wardrobe.wardrobe import CATEGORIES
from mascot_wardrobe.wardrobe.profile_photo import DEFAULT_BACKGROUND, SIZE, compose_profile_jpeg

# The scheduler's updater writes ``updated_at_utc``; ``current_time_utc`` is only a fallback.
TIMESTAMP_KEYS = ("updated_at_utc", "current_time_utc")
MAX_STATE_BYTES = 65536
DEFAULT_MAX_AGE = timedelta(hours=3)  # same freshness window as the live Open-Meteo reader
MAX_FUTURE = timedelta(minutes=15)


@dataclass(frozen=True)
class StateReading:
    temperature_c: float
    observed_at: datetime
    source: str = "state-file"


def overlay_for_date(overlay, start, end, *, now=None):
    """Enable external artwork within an optional inclusive UTC calendar-date window."""
    if start is None and end is None:
        return overlay
    if overlay is None or start is None or end is None:
        raise ValueError("An overlay date window requires --overlay and both date bounds")
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (ValueError, TypeError) as error:
        raise ValueError("Overlay dates must be YYYY-MM-DD") from error
    if first > last:
        raise ValueError("Overlay start date must not follow its end date")
    today = (now or datetime.now(UTC)).astimezone(UTC).date()
    return overlay if first <= today <= last else None


def _preflight_whatsapp(target, slug):
    from mascot_wardrobe.infrastructure.inventory_store import _safe_path

    catalog = _safe_path(target.catalog_dir)
    if not catalog.is_dir():
        raise ValueError("The approved WhatsApp photo directory must already exist")
    destination = _safe_path(catalog / (slug + ".jpg"))
    if destination.exists() and not destination.is_file():
        raise ValueError("Unsafe WhatsApp catalog target")


def _observed_at(value):
    if not isinstance(value, str):
        raise ValueError("State timestamp must be an ISO 8601 string")
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("State timestamp is invalid") from None
    if stamp.tzinfo is None:
        raise ValueError("State timestamp needs a UTC offset")
    return stamp


def read_weather_state(path, *, now=None, max_age=DEFAULT_MAX_AGE):
    """Read the updater's state JSON (``temperature_c`` plus ``updated_at_utc``); no network.

    The first timestamp key present is the only one used, so a stale ``updated_at_utc`` is never
    rescued by a fresh fallback. Stale, future, naive or malformed state raises ``ValueError``.
    """
    now = now or datetime.now(UTC)
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Weather state must be a regular file")
    if path.stat().st_size > MAX_STATE_BYTES:
        raise ValueError("Weather state file too large")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Weather state is not valid JSON") from error
    key = next((k for k in TIMESTAMP_KEYS if isinstance(data, dict) and k in data), None)
    if key is None or "temperature_c" not in data:
        raise ValueError("Weather state needs temperature_c and updated_at_utc")
    temperature = validate_temperature(data["temperature_c"])
    observed = _observed_at(data[key])
    age = now - observed
    if age > max_age:
        raise ValueError(f"Weather state is stale ({int(age.total_seconds() // 60)} minutes old)")
    if age < -MAX_FUTURE:
        raise ValueError("Weather state timestamp is in the future")
    return StateReading(temperature, observed)


def _category_map(closet, wardrobe):
    entries = closet.wardrobe_entries(wardrobe, renderer_check=False)
    items = closet.items()
    by_category = {}
    for entry in entries:
        template = items[entry["id"]].get("template")
        if template not in CATEGORIES:
            raise ValueError(f"Item {entry['id']} has an unsupported category")
        by_category.setdefault(template, []).append(entry["id"])
    missing = [c for c in CATEGORIES if c not in by_category]
    duplicate = [c for c, ids in by_category.items() if len(ids) > 1]
    if missing or duplicate:
        raise ValueError(
            f"Wardrobe {wardrobe} must map exactly six categories once each; "
            f"missing: {', '.join(missing) or 'none'}; duplicate: {', '.join(duplicate) or 'none'}"
        )
    return {category: ids[0] for category, ids in by_category.items()}


def select_closet_look(root, wardrobe, country, temperature, unit="C", *, profiles=None):
    """Pick the one saved item whose category the country profile gives this temperature."""
    profile = load_profile(country, profiles)
    temperature_c = to_celsius(temperature, unit)
    closet = Closet.open(root, renderer_check=False)
    mapping = _category_map(closet, wardrobe)
    category = profile.classify(temperature_c).value
    item_id = mapping[category]
    return {
        "wardrobe": wardrobe,
        "category": category,
        "item_id": item_id,
        "slug": f"closet-{wardrobe}-{item_id}",
        "temperature_c": temperature_c,
        "country_code": profile.country_code,
        "asset": str(closet.item_path(item_id)),
        "asset_sha256": closet.items()[item_id]["sha256"],
    }


def publish_closet_look(
    root,
    wardrobe,
    country,
    temperature,
    unit="C",
    *,
    weather=None,
    profiles=None,
    background=DEFAULT_BACKGROUND,
    overlay=None,
    apply=False,
    whatsapp=None,
    hermes=None,
    gateways=None,
):
    """Plan in memory; ``apply`` gates active PetDex selection and WhatsApp own-profile writes.

    ``hermes`` may inject an adapter. No Hermes runtime is loaded or called during dry-run.
    All image/configuration preflight completes before the first selected gateway writes.
    """
    from mascot_wardrobe.application import publish_outfit
    from mascot_wardrobe.application.collections import validate_gateways
    from mascot_wardrobe.channels import _slug

    if gateways is None:
        gateways = (["hermes"] if hermes is not None else []) + (["whatsapp"] if whatsapp else [])
    else:
        gateways = validate_gateways(gateways)
    if apply and not gateways:
        raise ValueError("Applying requires a selected gateway or the WhatsApp settings")
    if apply and "whatsapp" in gateways and whatsapp is None:
        raise ValueError("Applying the selected whatsapp gateway requires the WhatsApp settings")
    look = select_closet_look(root, wardrobe, country, temperature, unit, profiles=profiles)
    _slug(look["slug"])
    png = Path(look["asset"]).read_bytes()
    if hashlib.sha256(png).hexdigest() != look["asset_sha256"]:
        raise ValueError("Item asset changed while it was being read")
    jpeg = compose_profile_jpeg(png, background=background, overlay=overlay)
    channels = {}
    targets = []
    if "hermes" in gateways:
        from mascot_wardrobe.wardrobe.petdex import build_atlas

        atlas = build_atlas(png)
        channels["hermes"] = {
            "status": "planned",
            "target": "active-hermes-petdex",
            "remote_sync": False,
            "slug": atlas.slug,
            "atlas_sha256": atlas.sha256,
            "source_sha256": atlas.source_sha256,
            "width": 1536,
            "height": 1872,
            "frame_width": 192,
            "frame_height": 208,
            "columns": 8,
            "frames_per_state": 6,
            "rows": list(atlas.rows),
            "background_removed": atlas.background_removed,
        }
        targets.append("the active Hermes PetDex pet/avatar")
    if "whatsapp" in gateways:
        channels["whatsapp"] = {"status": "planned"}
        targets.append("the WhatsApp profile photo")
    target = " and ".join(targets) or "no channel (none configured)"
    if apply:
        if "whatsapp" in gateways:
            _preflight_whatsapp(whatsapp, look["slug"])
        if "hermes" in gateways:
            if hermes is None:
                from mascot_wardrobe.channels.hermes_pet import HermesPetAdapter

                hermes = HermesPetAdapter()
            prepared = hermes.preflight(atlas)
            try:
                channels["hermes"].update(hermes.apply(atlas, prepared=prepared), status="applied")
            except (ValueError, OSError, RuntimeError) as error:
                channels["hermes"].update(status="failed", error=str(error))
        if "whatsapp" in gateways:
            with tempfile.TemporaryDirectory(prefix="closet-look-") as staging:
                staged = Path(staging) / (look["slug"] + ".jpg")
                staged.write_bytes(jpeg)
                selection = {"slug": look["slug"], "category": look["category"], "jpg": str(staged)}
                published = publish_outfit(selection, apply=True, whatsapp=whatsapp)
                channels.update(published["channels"])
        ok = all(channel["status"] != "failed" for channel in channels.values())
        report = _report(look, weather, background, jpeg, "apply", channels, ok)
        report["overlay"] = overlay is not None
        report["action"] = f"attempted delivery to {target}; see channels for outcomes"
        return report
    report = _report(look, weather, background, jpeg, "dry-run", channels, True)
    report["overlay"] = overlay is not None
    report["action"] = f"would publish {look['slug']} to {target}; pass --apply to do it"
    return report


def _report(look, weather, background, jpeg, mode, channels, ok):
    return {
        "mode": mode,
        **{k: look[k] for k in ("wardrobe", "category", "item_id", "slug", "temperature_c", "country_code")},
        "weather": weather,
        "background": background.lower(),
        "width": SIZE,
        "height": SIZE,
        "jpg_sha256": hashlib.sha256(jpeg).hexdigest(),
        "channels": channels,
        "ok": ok,
    }
