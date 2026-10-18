"""Country policy repository, separate from domain and geography providers."""

import json
from pathlib import Path

from mascot_wardrobe.domain.models import CountryProfile


def load_profile(country_code, path=None):
    country_code = country_code.upper()
    config = Path(path) if path is not None else Path(__file__).with_name("profiles.json")
    if config.stat().st_size > 262144:
        raise ValueError("Country profiles file too large")
    data = json.loads(config.read_text(encoding="utf-8"))
    try:
        raw = data["profiles"][country_code]
        return CountryProfile(
            country_code, tuple(raw["thresholds_c"]), tuple(raw["palette"]), tuple(raw["garments"])
        )
    except (KeyError, TypeError) as exc:
        raise ValueError("Country profile not configured; supply a reviewed custom profiles file") from exc
