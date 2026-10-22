"""Style registry, palette roles and the deterministic resolvers."""

import re
import subprocess
import sys
import unittest

from mascot_wardrobe.wardrobe.styles import (
    AUTO,
    AUTO_PALETTES,
    PROVENANCE,
    STYLES,
    PaletteRoles,
    StyleChoice,
    palette_roles,
    resolve_palette,
    resolve_style,
    validate_palette,
    validate_provenance,
    validate_style,
)

PALETTE_3 = ("#101014", "#4920A8", "#9B4DFF", "#DDC5FF")  # example and fixture only, never a default
HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def rgb(color):
    return tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))


class RolesTests(unittest.TestCase):
    def test_roles_for_one_to_six_colours(self):
        colors = ["#010101", "#020202", "#030303", "#040404", "#050505", "#060606"]
        # (base, secondary, accent, highlight) as indexes into the palette
        expected = {1: (0, 0, 0, 0), 2: (0, 0, 1, 1), 3: (0, 1, 2, 2), 4: (0, 1, 3, 2), 6: (0, 1, 5, 4)}
        for count, picks in expected.items():
            with self.subTest(count=count):
                roles = palette_roles(colors[:count])
                self.assertEqual(roles, PaletteRoles(*(rgb(colors[i]) for i in picks)))

    def test_palette_3_fills_the_four_roles(self):
        roles = palette_roles(PALETTE_3)
        self.assertEqual(roles.base, rgb("#101014"))
        self.assertEqual(roles.secondary, rgb("#4920A8"))
        self.assertEqual(roles.accent, rgb("#DDC5FF"))
        self.assertEqual(roles.highlight, rgb("#9B4DFF"))

    def test_roles_reject_invalid_palettes(self):
        with self.assertRaises(ValueError):
            palette_roles(["red"])


class ValidationTests(unittest.TestCase):
    def test_valid_values(self):
        for style in STYLES:
            self.assertEqual(validate_style(style), style)
        self.assertEqual(validate_palette(["#12ab34"]), ("#12ab34",))
        self.assertEqual(len(validate_palette(["#000000"] * 6)), 6)
        for source in PROVENANCE:
            self.assertEqual(validate_provenance(source), source)

    def test_invalid_values_raise_value_error(self):
        for style in ("grunge", "", "auto", None, 3, "Classic"):
            with self.subTest(style=style), self.assertRaises(ValueError):
                validate_style(style)
        bad = (["#12"], ["red"], ["#12345g"], ["#1234567"], ["#000000"] * 7, [], (), "#101014", None, [3])
        for palette in bad:
            with self.subTest(palette=palette), self.assertRaises(ValueError):
                validate_palette(palette)
        for source in ("guess", "", None, 1):
            with self.subTest(source=source), self.assertRaises(ValueError):
                validate_provenance(source)

    def test_style_choice_is_a_frozen_record(self):
        choice = StyleChoice("classic", ("#101014",), "user", "auto")
        with self.assertRaises(AttributeError):
            choice.style = "streetwear"


class ResolverTests(unittest.TestCase):
    def test_explicit_values_have_user_provenance(self):
        self.assertEqual(resolve_style("streetwear", "cold", "seed"), ("streetwear", "user"))
        self.assertEqual(
            resolve_palette(["#101014", "#ffffff"], "classic", "cold", "seed"),
            (("#101014", "#ffffff"), "user"),
        )

    def test_resolvers_reject_invalid_input(self):
        calls = (
            lambda: resolve_style("grunge", "cold", "seed"),
            lambda: resolve_style(None, "cold", "seed"),
            lambda: resolve_style(AUTO, "cold", 7),
            lambda: resolve_palette("red", "classic", "cold", "seed"),
            lambda: resolve_palette([AUTO, "#101014"], "classic", "cold", "seed"),
            lambda: resolve_palette(None, "classic", "cold", "seed"),
            lambda: resolve_palette(AUTO, "grunge", "cold", "seed"),
            lambda: resolve_palette(["#12"], "classic", "cold", "seed"),
        )
        for call in calls:
            with self.assertRaises(ValueError):
                call()

    def test_auto_is_deterministic_and_marked_auto(self):
        for seed in ("a", "b", "c"):
            style, source = resolve_style(AUTO, "catalog", seed)
            self.assertEqual(source, "auto")
            self.assertEqual((style, source), resolve_style(AUTO, "catalog", seed))
            palette, palette_source = resolve_palette(AUTO, style, "catalog", seed)
            self.assertEqual(palette_source, "auto")
            self.assertEqual((palette, palette_source), resolve_palette(AUTO, style, "catalog", seed))

    def test_auto_is_stable_in_a_fresh_process(self):
        code = (
            "from mascot_wardrobe.wardrobe.styles import resolve_style, resolve_palette\n"
            "s = resolve_style('auto', 'warm', 'seed-1')[0]\n"
            "print(s, *resolve_palette('auto', s, 'warm', 'seed-1')[0])\n"
        )
        outputs = {
            subprocess.run(
                [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
            ).stdout
            for _ in range(2)
        }
        self.assertEqual(len(outputs), 1)
        style = resolve_style(AUTO, "warm", "seed-1")[0]
        palette = resolve_palette(AUTO, style, "warm", "seed-1")[0]
        self.assertEqual(outputs.pop().split(), [style, *palette])

    def test_auto_varies_across_seeds_and_stays_valid(self):
        styles, palettes = set(), set()
        for number in range(200):
            for garment in ("catalog", "cold", "very_hot"):
                style, _ = resolve_style(AUTO, garment, f"seed-{number}")
                palette, _ = resolve_palette(AUTO, style, garment, f"seed-{number}")
                self.assertIn(style, STYLES)
                self.assertEqual(palette, validate_palette(palette))
                self.assertTrue(all(HEX.fullmatch(c) for c in palette))
                self.assertEqual(len(palette), 4)
                self.assertIn(palette, AUTO_PALETTES[style])
                styles.add(style)
                palettes.add(palette)
        self.assertEqual(styles, set(STYLES))
        self.assertGreater(len(palettes), len(STYLES))

    def test_curated_tables_cover_every_style_and_are_valid(self):
        self.assertEqual(set(AUTO_PALETTES), set(STYLES))
        for options in AUTO_PALETTES.values():
            self.assertGreaterEqual(len(options), 2)
            for palette in options:
                self.assertEqual(validate_palette(palette), palette)
                self.assertEqual(len(palette), 4)


if __name__ == "__main__":
    unittest.main()
