import unittest


class DomainTests(unittest.TestCase):
    def test_six_ordered_categories_with_br_boundary_on_upper_band(self):
        try:
            from mascot_wardrobe.domain.models import Category, CountryProfile
        except ImportError as exc:
            self.fail(f"Category domain not implemented: {exc}")
        profile = CountryProfile("BR", (5, 15, 20, 25, 32), ("#123456",) * 6, ("garment",) * 6)
        expected = [
            Category.VERY_COLD,
            Category.COLD,
            Category.COLD,
            Category.MILD,
            Category.AMBIENT,
            Category.WARM,
            Category.VERY_HOT,
        ]
        self.assertEqual(len(Category), 6)
        self.assertEqual([profile.classify(v) for v in [-20, 5, 14.99, 15, 20, 25, 32]], expected)

    def test_invalid_policy_and_temperatures_are_rejected(self):
        from mascot_wardrobe.domain.models import CountryProfile

        for thresholds in [(5, 15), (5, 5, 20, 25, 32), (5, 15, 20, 25, float("nan"))]:
            with self.subTest(thresholds=thresholds), self.assertRaises(ValueError):
                CountryProfile("BR", thresholds, ("#123456",) * 6, ("garment",) * 6)
        profile = CountryProfile("BR", (5, 15, 20, 25, 32), ("#123456",) * 6, ("garment",) * 6)
        for invalid in [True, float("nan"), float("inf"), -100, 80, "20"]:
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                profile.classify(invalid)
        with self.assertRaises(ValueError):
            CountryProfile("../", (5, 15, 20, 25, 32), ("#123456",) * 6, ("garment",) * 6)

    def test_mascot_disallows_traversal_and_invalid_body_geometry(self):
        from pathlib import Path

        from mascot_wardrobe.domain.models import Mascot

        for slug in ["../evil", "foo/bar", "", "Mascot"]:
            with self.subTest(slug=slug), self.assertRaises(ValueError):
                Mascot(slug, Path("mascot.png"))
        for box in [(0.8, 0.6, 0.2, 0.9), (0.1, 0.2, 1.5, 0.9), (0.1, 0.6, 0.8, float("nan"))]:
            with self.subTest(box=box), self.assertRaises(ValueError):
                Mascot("test", Path("mascot.png"), box)

    def test_country_profiles_select_different_ranges_and_unknown_country_fails(self):
        try:
            from mascot_wardrobe.infrastructure.policies import load_profile
        except ImportError as exc:
            self.fail(f"Country policy loader missing: {exc}")
        self.assertNotEqual(load_profile("BR").classify(24), load_profile("GB").classify(24))
        for code in ["BR", "GB", "US", "JP"]:
            self.assertEqual(load_profile(code).country_code, code)
        with self.assertRaises(ValueError):
            load_profile("ZZ")

    def test_fahrenheit_conversion_is_explicit(self):
        try:
            from mascot_wardrobe.domain.models import to_celsius
        except ImportError as exc:
            self.fail(f"Temperature unit conversion missing: {exc}")
        self.assertAlmostEqual(to_celsius(68, "F"), 20)
        self.assertEqual(to_celsius(20, "C"), 20)
        with self.assertRaises(ValueError):
            to_celsius(20, "K")
        with self.assertRaises(ValueError):
            to_celsius(True, "F")
