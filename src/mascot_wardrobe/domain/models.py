"""Pure domain values. No file I/O, network or provider imports."""

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Category(StrEnum):
    VERY_COLD = "very_cold"
    COLD = "cold"
    MILD = "mild"
    AMBIENT = "ambient"
    WARM = "warm"
    VERY_HOT = "very_hot"

    @property
    def label(self):
        return self.value.replace("_", " ")


@dataclass(frozen=True)
class CountryProfile:
    country_code: str
    thresholds_c: tuple[float, ...]
    palette: tuple[str, ...]
    garments: tuple[str, ...]

    def __post_init__(self):
        if not isinstance(self.country_code, str) or re.fullmatch(r"[A-Z]{2}", self.country_code) is None:
            raise ValueError("Country must be an uppercase two-letter code")
        for name in ("thresholds_c", "palette", "garments"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if len(self.thresholds_c) != 5:
            raise ValueError("Six categories require exactly five boundaries")
        for temperature in self.thresholds_c:
            validate_temperature(temperature)
        if any(a >= b for a, b in zip(self.thresholds_c, self.thresholds_c[1:], strict=False)):
            raise ValueError("Temperature boundaries must be strictly increasing")
        if len(self.palette) != 6 or any(
            not isinstance(c, str) or re.fullmatch(r"#[a-fA-F0-9]{6}", c) is None for c in self.palette
        ):
            raise ValueError("Exactly six hexadecimal garment colors required")
        if len(self.garments) != 6 or any(
            not isinstance(g, str) or not g.strip() or len(g) > 500 for g in self.garments
        ):
            raise ValueError("Exactly six nonempty garment briefs required")

    def classify(self, temperature_c: float) -> Category:
        validate_temperature(temperature_c)
        return tuple(Category)[sum(temperature_c >= bound for bound in self.thresholds_c)]


@dataclass(frozen=True)
class Mascot:
    mascot_id: str
    image_path: Path
    body_box: tuple[float, float, float, float] = (0.22, 0.60, 0.78, 0.88)

    def __post_init__(self):
        if (
            not isinstance(self.mascot_id, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", self.mascot_id) is None
        ):
            raise ValueError("Mascot id must be a short lowercase slug")
        object.__setattr__(self, "image_path", Path(self.image_path))
        object.__setattr__(self, "body_box", tuple(self.body_box))
        if len(self.body_box) != 4 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1
            for v in self.body_box
        ):
            raise ValueError("Body box must contain four finite normalized coordinates")
        x1, y1, x2, y2 = self.body_box
        if x1 >= x2 or y1 >= y2:
            raise ValueError("Body box must have positive width and height")


def validate_temperature(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not -90 <= value <= 65
    ):
        raise ValueError("Expected finite plausible Celsius temperature between -90 and 65")
    return float(value)


def to_celsius(value, unit="C"):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Temperature must be a finite number")
    if unit not in {"C", "F"}:
        raise ValueError("Supported temperature units are C and F")
    return validate_temperature((value - 32) * 5 / 9 if unit == "F" else value)
