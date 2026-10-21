"""Style registry, palette roles and the deterministic resolvers for ``auto`` choices.

Standard library only. The resolvers never use randomness, a clock or the environment.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass

STYLES = ("classic", "streetwear")  # registry order is the auto-resolution order
AUTO = "auto"
PROVENANCE = ("user", "auto", "default", "profile", "migrated")

_HEX = re.compile(r"#[0-9A-Fa-f]{6}")

# Curated four-colour sets (base, secondary, accent, highlight) for ``auto``.
AUTO_PALETTES = {
    "classic": (
        ("#2878b5", "#4f9bd9", "#e8b84b", "#f7e3a1"),
        ("#b5472f", "#d9795f", "#f2d16b", "#fbeec1"),
        ("#3f7d4e", "#6fae7d", "#e6c25a", "#f4ecc0"),
        ("#6b5b95", "#9c8cc4", "#f0b27a", "#fae5d3"),
    ),
    "streetwear": (
        ("#101014", "#4920a8", "#9b4dff", "#ddc5ff"),
        ("#1c1f26", "#c8452d", "#f2f2f2", "#ffd166"),
        ("#26303b", "#3d8f7a", "#e8e8e8", "#9ef0d0"),
        ("#2b2b2b", "#e0b020", "#fafafa", "#ff6b35"),
    ),
}


@dataclass(frozen=True)
class PaletteRoles:
    base: tuple[int, int, int]
    secondary: tuple[int, int, int]
    accent: tuple[int, int, int]
    highlight: tuple[int, int, int]


@dataclass(frozen=True)
class StyleChoice:
    """Already-resolved values handed to ``WardrobeAgent.generate``."""

    style: str
    palette: tuple[str, ...]
    style_choice: str
    palette_choice: str


def _rgb(color: str) -> tuple[int, int, int]:
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


def validate_style(value: str) -> str:
    if not isinstance(value, str) or value not in STYLES:
        raise ValueError(f"Unknown style; use one of {', '.join(STYLES)}.")
    return value


def validate_palette(value) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("The palette must be a list of 1 to 6 #RRGGBB colors.")
    if not 1 <= len(value) <= 6:
        raise ValueError("The palette needs 1 to 6 #RRGGBB colors.")
    for color in value:
        if not isinstance(color, str) or not _HEX.fullmatch(color):
            raise ValueError("Each palette color must be a #RRGGBB hex string.")
    return tuple(value)


def validate_provenance(value: str) -> str:
    if not isinstance(value, str) or value not in PROVENANCE:
        raise ValueError(f"Unknown choice source; use one of {', '.join(PROVENANCE)}.")
    return value


def palette_roles(palette: Sequence[str]) -> PaletteRoles:
    p = validate_palette(palette)
    base, accent = p[0], p[-1]
    secondary = p[1] if len(p) >= 3 else p[0]
    highlight = p[-2] if len(p) >= 4 else accent
    return PaletteRoles(_rgb(base), _rgb(secondary), _rgb(accent), _rgb(highlight))


def _pick(seed: str, garment: str, kind: str, size: int) -> int:
    for part in (seed, garment):
        if not isinstance(part, str):
            raise ValueError("The seed and the garment must be strings.")
    return int(hashlib.sha256(f"{seed}|{garment}|{kind}".encode()).hexdigest(), 16) % size


def resolve_style(style: str, garment: str, seed: str) -> tuple[str, str]:
    """Return ``(style, "user" | "auto")``; ``style`` is a STYLES value or AUTO."""
    if style == AUTO:
        return STYLES[_pick(seed, garment, "style", len(STYLES))], "auto"
    return validate_style(style), "user"


def resolve_palette(palette, style: str, garment: str, seed: str) -> tuple[tuple[str, ...], str]:
    """Return ``(palette, "user" | "auto")``; ``palette`` is a hex sequence or AUTO."""
    if isinstance(palette, str):
        if palette != AUTO:
            raise ValueError("The palette must be a list of colors or 'auto'.")
        options = AUTO_PALETTES[validate_style(style)]
        return options[_pick(seed, garment, f"palette|{style}", len(options))], "auto"
    return validate_palette(palette), "user"
