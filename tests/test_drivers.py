from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from paddock.drivers import (
    DRIVERS,
    DriverError,
    detect,
    document_path,
    document_root,
    normalize_document_root,
    resolve,
)


class DetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def project(self, *files: str) -> Path:
        for name in files:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x", encoding="utf-8")
        return self.root

    def test_laravel_is_detected_from_a_committed_marker(self) -> None:
        # Every marker must exist in a fresh clone, before any dependency is
        # installed, or linking a just-cloned repository misidentifies it.
        self.assertEqual("laravel", detect(self.project("artisan")).name)

    def test_statamic_wins_over_laravel_because_it_ships_both(self) -> None:
        self.assertEqual("statamic", detect(self.project("artisan", "please")).name)

    def test_wordpress_is_detected_and_served_from_its_own_directory(self) -> None:
        driver = detect(self.project("wp-config.php"))
        self.assertEqual("wordpress", driver.name)
        self.assertEqual(".", document_root(self.root, driver))

    def test_symfony_needs_both_markers(self) -> None:
        self.assertEqual(
            "symfony", detect(self.project("bin/console", "public/index.php")).name
        )
        # bin/console alone is not enough; plenty of projects have one.
        other = Path(tempfile.mkdtemp(dir=self.root))
        (other / "bin").mkdir()
        (other / "bin/console").write_text("x", encoding="utf-8")
        self.assertEqual("static", detect(other).name)

    def test_generic_php_covers_both_common_layouts(self) -> None:
        nested = self.project("public/index.php")
        self.assertEqual("php", detect(nested).name)
        self.assertEqual("public", document_root(nested, detect(nested)))

        flat = Path(tempfile.mkdtemp(dir=self.root))
        (flat / "index.php").write_text("x", encoding="utf-8")
        self.assertEqual("php", detect(flat).name)
        self.assertEqual(".", document_root(flat, detect(flat)))

    def test_an_unrecognized_directory_is_static(self) -> None:
        self.assertEqual("static", detect(self.project("index.html")).name)

    def test_static_runs_nothing(self) -> None:
        self.assertFalse(DRIVERS["static"].php)
        self.assertTrue(all(
            driver.php for name, driver in DRIVERS.items() if name != "static"
        ))

    def test_document_root_prefers_an_existing_candidate(self) -> None:
        driver = DRIVERS["static"]
        self.assertEqual(".", document_root(self.root, driver))
        (self.root / "dist").mkdir()
        self.assertEqual("dist", document_root(self.root, driver))
        (self.root / "public").mkdir()
        self.assertEqual("public", document_root(self.root, driver))

    def test_document_root_falls_back_rather_than_raising(self) -> None:
        # Parking reconciliation runs on a filesystem event and must answer for
        # a folder that is mid-clone rather than raise.
        self.assertEqual("public", document_root(self.root, DRIVERS["laravel"]))


class OverrideTests(unittest.TestCase):
    def test_an_override_is_normalized(self) -> None:
        self.assertEqual("web", normalize_document_root("web"))
        self.assertEqual("web/public", normalize_document_root("./web/public"))
        self.assertEqual(".", normalize_document_root("."))

    def test_an_override_cannot_leave_the_project(self) -> None:
        # It arrives from a committed file, so a clone must not be able to
        # point a .test host at anything outside its own directory.
        for escape in ("/etc", "..", "../sibling", "public/../..", ""):
            with self.subTest(escape=escape), self.assertRaises(DriverError):
                normalize_document_root(escape)

    def test_document_path_resolves_the_project_root_marker(self) -> None:
        root = Path("/projects/app")
        self.assertEqual(root, document_path(root, "."))
        self.assertEqual(root / "public", document_path(root, "public"))

    def test_an_unknown_type_names_the_supported_ones(self) -> None:
        with self.assertRaisesRegex(DriverError, "laravel"):
            resolve("drupal")


if __name__ == "__main__":
    unittest.main()
