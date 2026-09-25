"""Offline schema tests; signature and refresh tests arrive in Unit 2."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from paddock.runtime_catalog import CATALOG_URLS, RuntimeCatalog, RuntimeCatalogError


FIXTURES = Path(__file__).parent / "fixtures/runtime_catalogs"


class RuntimeCatalogTests(unittest.TestCase):
    def load_modified(self, fixture: str, kind: str, change) -> RuntimeCatalog:
        document = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
        change(document)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            return RuntimeCatalog.load(path, kind)

    def test_stable_urls_and_valid_fixtures(self) -> None:
        self.assertEqual(
            "https://lukeska.github.io/paddock/runtime-catalogs/php.json",
            CATALOG_URLS["php"],
        )
        php = RuntimeCatalog.load(FIXTURES / "php-valid.json", "php")
        node = RuntimeCatalog.load(FIXTURES / "node-valid.json", "node")
        older = RuntimeCatalog.load(FIXTURES / "php-older.json", "php")
        self.assertEqual(("php", 2, "8.4.25"), (php.kind, php.revision, php.manifest.artifacts[0].php))
        self.assertEqual(("node", 2, "24.20.0"), (node.kind, node.revision, node.manifest.artifacts[0].node))
        self.assertLess(older.revision, php.revision)

    def test_insecure_fixture_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeCatalogError, "HTTPS"):
            RuntimeCatalog.load(FIXTURES / "php-invalid.json", "php")

    def test_revision_and_top_level_schema_are_strict(self) -> None:
        for revision in (0, -1, True, "2"):
            with self.subTest(revision=revision), self.assertRaises(RuntimeCatalogError):
                self.load_modified("php-valid.json", "php", lambda data: data.update(revision=revision))
        with self.assertRaisesRegex(RuntimeCatalogError, "invalid fields"):
            self.load_modified("php-valid.json", "php", lambda data: data.update(extra=True))
        with self.assertRaisesRegex(RuntimeCatalogError, "invalid schema"):
            self.load_modified("php-valid.json", "php", lambda data: data.update(schema_version=True))

    def test_duplicate_line_and_architecture_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeCatalogError, "duplicate php 8.4"):
            self.load_modified("php-valid.json", "php", lambda data: data["artifacts"].append(dict(data["artifacts"][0])))

    def test_immutable_php_and_exact_node_urls_are_required(self) -> None:
        for url in (
            "https://github.com/lukeska/paddock/releases/latest/download/paddock-php-8.4.25-linux-x86_64.tar.gz",
            "https://evil.example/paddock-php-8.4.25-linux-x86_64.tar.gz",
            "https://github.com/lukeska/paddock/releases/download/php-2026.09.20/paddock-php-8.4.25-linux-x86_64.tar.gz?x=1",
            "\nhttps://github.com/lukeska/paddock/releases/download/php-2026.09.20/paddock-php-8.4.25-linux-x86_64.tar.gz",
        ):
            with self.subTest(url=url), self.assertRaises(RuntimeCatalogError):
                self.load_modified("php-valid.json", "php", lambda data: data["artifacts"][0].update(url=url))
        with self.assertRaisesRegex(RuntimeCatalogError, "exact official release"):
            self.load_modified("node-valid.json", "node", lambda data: data["artifacts"][0].update(url="https://nodejs.org/download/release/latest/node.tar.xz"))

    def test_empty_or_bad_artifacts_are_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeCatalogError, "must contain artifacts"):
            self.load_modified("node-valid.json", "node", lambda data: data.update(artifacts=[]))
        with self.assertRaisesRegex(RuntimeCatalogError, "invalid node artifact"):
            self.load_modified("node-valid.json", "node", lambda data: data["artifacts"][0].update(major="22"))


if __name__ == "__main__":
    unittest.main()
