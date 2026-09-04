from __future__ import annotations

from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from paddock.composer import ComposerInstallError, install_composer


class ComposerInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.payload = b"composer phar fixture"
        self.catalog = self.root / "composer.json"
        self.catalog.write_text(json.dumps({
            "schema_version": 1,
            "version": "2.10.3",
            "url": "https://example.test/composer.phar",
            "sha256": hashlib.sha256(self.payload).hexdigest(),
        }), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_installs_verified_phar_and_is_idempotent(self) -> None:
        calls = []

        def open_fixture(url):
            calls.append(url)
            return closing(io.BytesIO(self.payload))

        data = self.root / "data"
        self.assertEqual("2.10.3", install_composer(data, self.catalog, open_fixture))
        self.assertIsNone(install_composer(data, self.catalog, open_fixture))
        self.assertEqual(self.payload, (data / "composer/composer.phar").read_bytes())
        self.assertEqual(1, len(calls))

    def test_rejects_a_checksum_mismatch_without_activating_it(self) -> None:
        raw = json.loads(self.catalog.read_text(encoding="utf-8"))
        raw["sha256"] = "0" * 64
        self.catalog.write_text(json.dumps(raw), encoding="utf-8")
        data = self.root / "data"
        with self.assertRaisesRegex(ComposerInstallError, "checksum mismatch"):
            install_composer(data, self.catalog, lambda url: closing(io.BytesIO(self.payload)))
        self.assertFalse((data / "composer/composer.phar").exists())


class PackagedComposerCatalogTests(unittest.TestCase):
    def test_catalog_pins_an_immutable_official_release(self) -> None:
        catalog = json.loads(
            (Path(__file__).parents[1] / "resources/composer.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            f"https://getcomposer.org/download/{catalog['version']}/composer.phar",
            catalog["url"],
        )
        self.assertRegex(catalog["sha256"], r"^[0-9a-f]{64}$")
