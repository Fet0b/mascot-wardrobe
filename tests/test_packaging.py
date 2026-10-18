import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_public_surface_has_no_machine_specific_roots(self):
        from mascot_wardrobe import application

        self.assertFalse(hasattr(application, "PROJECT_ROOT"))
        self.assertFalse(hasattr(application, "SCRATCH_ROOT"))

    def test_distribution_metadata_and_entry_point(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text())
        project = config["project"]
        self.assertEqual(project["name"], "mascot-wardrobe")
        self.assertEqual(project["license"], "MIT")
        self.assertEqual(project["license-files"], ["LICENSE"])
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertFalse([c for c in project.get("classifiers", []) if c.startswith("License ::")])
        self.assertEqual(project["scripts"]["mascot-wardrobe"], "mascot_wardrobe.interfaces.cli:main")
        requires = config["build-system"]["requires"]
        self.assertIn("setuptools>=77", requires)
        for extra in ("pytest", "ruff", "detect-secrets", "build"):
            self.assertTrue(any(d.startswith(extra) for d in project["optional-dependencies"]["dev"]), extra)
        self.assertIn("select", config["tool"]["ruff"]["lint"])
        self.assertNotIn("select", config["tool"]["ruff"])

    def test_wheel_includes_reviewed_country_profiles(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text())
        package_data = config["tool"]["setuptools"].get("package-data", {})
        self.assertIn("profiles.json", package_data.get("mascot_wardrobe.infrastructure", []))

    def test_generated_output_directory_is_ignored_by_git(self):
        self.assertIn("mascot-wardrobe-out/", (ROOT / ".gitignore").read_text().splitlines())
