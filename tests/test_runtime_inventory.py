from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from paddock.application import PaddockController, _catalog_stale
from paddock.catalog_store import CatalogSelection
from paddock.node_runtime import NodeRegistry
from paddock.paths import Paths
from paddock.runtimes import RuntimeRegistry
from paddock.schemas import SchemaError, validate_runtimes
from paddock.state import StateStore


class RuntimeInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        paths = Paths.from_environment({
            "HOME": str(base / "home"), "XDG_CONFIG_HOME": str(base / "config"),
            "XDG_DATA_HOME": str(base / "data"), "XDG_STATE_HOME": str(base / "state"),
            "XDG_CACHE_HOME": str(base / "cache"),
        }, runtime_root=base / "run")
        self.store = StateStore(paths)
        self.store.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def managed(self, kind: str, release: str, digest: str = "a" * 64,
                reported: str | None = None) -> Path:
        line = ".".join(release.split(".")[:2]) if kind == "php" else release.split(".")[0]
        directory = ("runtimes" if kind == "php" else "node-runtimes")
        filename = "php" if kind == "php" else "node"
        executable = self.store.paths.data / directory / "releases" / f"{kind}-{release}-{digest[:12]}" / "bin" / filename
        executable.parent.mkdir(parents=True)
        value = reported or release
        executable.write_text(f"#!/bin/sh\necho {'v' if kind == 'node' else ''}{value}\n", encoding="utf-8")
        executable.chmod(0o755)
        record = {"version": line, "path": str(executable), "sha256": digest}
        self.store.write("runtimes" if kind == "php" else "node_runtimes",
                         {"schema_version": 1, "runtimes": {line: record}})
        return executable

    def test_legacy_managed_php_and_node_are_migrated_once(self) -> None:
        self.managed("php", "8.4.24")
        self.managed("node", "24.19.0")
        self.assertEqual("8.4.24", RuntimeRegistry(self.store).list()[0].release)
        self.assertEqual("24.19.0", NodeRegistry(self.store).list()[0].release)
        self.assertEqual("8.4.24", self.store.read("runtimes")["runtimes"]["8.4"]["release"])
        self.assertEqual("24.19.0", self.store.read("node_runtimes")["runtimes"]["24"]["release"])

    def test_mismatch_is_recorded_as_unknown_not_catalog_version(self) -> None:
        self.managed("php", "8.4.24", reported="8.4.23")
        self.assertIsNone(RuntimeRegistry(self.store).list()[0].release)
        self.assertIn("release", self.store.read("runtimes")["runtimes"]["8.4"])
        controller = PaddockController(self.store)
        view = next(item for item in controller.php_versions_snapshot().versions if item.minor == "8.4")
        self.assertEqual("8.4", view.release)
        self.assertIsNone(view.installed_release)
        self.assertFalse(view.update_available)

    def test_custom_runtime_is_not_executed_during_migration(self) -> None:
        marker = Path(self.temporary.name) / "executed"
        custom = Path(self.temporary.name) / "custom-php"
        custom.write_text(f"#!/bin/sh\ntouch {marker}\necho 8.4.24\n", encoding="utf-8")
        custom.chmod(0o755)
        self.store.write("runtimes", {"schema_version": 1, "runtimes": {
            "8.4": {"version": "8.4", "path": str(custom), "sha256": "a" * 64},
        }})
        self.assertIsNone(RuntimeRegistry(self.store).list()[0].release)
        self.assertFalse(marker.exists())

    def test_snapshot_compares_installed_and_available_patches(self) -> None:
        self.managed("php", "8.4.24")
        self.managed("node", "24.19.0")
        php = next(item for item in PaddockController(self.store).php_versions_snapshot().versions if item.minor == "8.4")
        node = next(item for item in PaddockController(self.store).node_versions_snapshot().versions if item.major == "24")
        self.assertEqual(("8.4.24", "8.4.25", True), (php.installed_release, php.available_release, php.update_available))
        self.assertEqual(("24.19.0", "24.20.0", True), (node.installed_release, node.available_release, node.update_available))

    def test_current_and_newer_installed_patches_are_not_updates(self) -> None:
        self.managed("php", "8.4.25")
        self.managed("node", "24.21.0")
        php = next(item for item in PaddockController(self.store).php_versions_snapshot().versions if item.minor == "8.4")
        node = next(item for item in PaddockController(self.store).node_versions_snapshot().versions if item.major == "24")
        self.assertFalse(php.update_available)
        self.assertFalse(node.update_available)
        self.assertEqual("24.21.0", node.release)

    def test_registry_rejects_mismatched_release(self) -> None:
        with self.assertRaisesRegex(SchemaError, "does not match"):
            validate_runtimes({"schema_version": 1, "runtimes": {"8.4": {
                "version": "8.4", "path": "/tmp/php", "sha256": "a" * 64,
                "release": "8.5.0",
            }}})

    def test_catalog_freshness_is_distinct_from_installed_patch(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        current = datetime.now(timezone.utc).isoformat()
        self.assertTrue(_catalog_stale(CatalogSelection(None, "refreshed", 2, old)))
        self.assertFalse(_catalog_stale(CatalogSelection(None, "refreshed", 2, current)))
        self.assertFalse(_catalog_stale(CatalogSelection(None, "bundled", None, None)))


if __name__ == "__main__":
    unittest.main()
