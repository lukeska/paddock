from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from paddock.paths import PathConfigurationError, Paths
from paddock.schemas import SchemaError, validate_sites
from paddock.state import StateError, StateStore


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.environment = {
            "HOME": str(base / "Home üser"),
            "XDG_CONFIG_HOME": str(base / "Config space"),
            "XDG_DATA_HOME": str(base / "Data ü"),
            "XDG_STATE_HOME": str(base / "State space"),
            "XDG_CACHE_HOME": str(base / "Cache ü"),
            "XDG_RUNTIME_DIR": str(base / "Runtime space"),
        }
        self.paths = Paths.from_environment(self.environment)
        self.store = StateStore(self.paths)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_initializes_v1_records_with_private_modes(self) -> None:
        self.store.initialize()
        for record in ("settings", "runtimes", "sites"):
            path = self.store.path_for(record)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(self.store.read(record)["schema_version"], 1)

    def test_update_is_atomic_and_leaves_no_candidate(self) -> None:
        self.store.initialize()
        path = self.store.path_for("settings")
        before = path.read_bytes()
        with patch("paddock.atomic.os.replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                self.store.write(
                    "settings", {"schema_version": 1, "default_php": "8.4"}
                )
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob(f".{path.name}.*")), [])

    def test_rejects_malformed_existing_state_without_replacing_it(self) -> None:
        self.store.initialize()
        path = self.store.path_for("sites")
        path.write_text('{"schema_version": 99, "sites": {}}', encoding="utf-8")
        before = path.read_bytes()
        with self.assertRaises(StateError):
            self.store.update("sites", lambda value: value)
        self.assertEqual(path.read_bytes(), before)

    def test_schema_rejects_relative_site_root_and_unknown_fields(self) -> None:
        with self.assertRaises(SchemaError):
            validate_sites(
                {
                    "schema_version": 1,
                    "sites": {
                        "demo": {
                            "name": "demo",
                            "root": "relative",
                            "php": "8.4",
                            "secured": True,
                            "extra": "no",
                        }
                    },
                }
            )

    def test_runtime_scope_is_required_only_when_requested(self) -> None:
        environment = {"HOME": self.environment["HOME"]}
        paths = Paths.from_environment(environment)
        self.assertIsNone(paths.runtime)
        with self.assertRaises(PathConfigurationError):
            Paths.from_environment(environment, require_runtime=True)

    def test_initialize_preserves_existing_valid_state(self) -> None:
        self.store.initialize()
        self.store.write("settings", {"schema_version": 1, "default_php": "8.5"})
        self.store.initialize()
        self.assertEqual(self.store.read("settings")["default_php"], "8.5")


if __name__ == "__main__":
    unittest.main()
