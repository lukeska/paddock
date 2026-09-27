from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SOURCE = Path(__file__).parent / "runtime-upgrade/fixture.py"
SPEC = importlib.util.spec_from_file_location("runtime_upgrade_fixture", SOURCE)
assert SPEC is not None and SPEC.loader is not None
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


class RuntimeUpgradeFixtureTests(unittest.TestCase):
    def test_node_catalog_pins_older_official_patch(self) -> None:
        artifact = json.loads(fixture.document("node"))["artifacts"][0]
        self.assertEqual("24.19.0", artifact["node"])
        self.assertEqual("24", artifact["major"])
        self.assertEqual(fixture.NODE_SHA256, artifact["sha256"])
        self.assertEqual("https://nodejs.org/download/release/v24.19.0/node-v24.19.0-linux-x64.tar.xz", artifact["url"])

    def test_php_catalog_pins_file_and_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "old.tar.gz"
            archive.write_bytes(b"fixture")
            artifact = json.loads(fixture.document("php", archive))["artifacts"][0]
            self.assertEqual("8.5.8", artifact["php"])
            self.assertEqual(archive.as_uri(), artifact["url"])
            self.assertEqual(fixture.digest(archive), artifact["sha256"])

    def test_stage_refuses_existing_override_and_unstage_checks_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            config = directory / "config"
            config.mkdir()
            with mock.patch.object(fixture, "fixture_payloads", return_value=(b"php", b"node")), \
                 mock.patch.object(fixture, "config_directory", return_value=config), \
                 mock.patch.object(fixture.os, "geteuid", return_value=1000):
                (config / "artifacts.json").write_bytes(b"user owned")
                with self.assertRaisesRegex(ValueError, "refusing to replace"):
                    fixture.stage(directory)
                (config / "artifacts.json").unlink()
                fixture.stage(directory)
                self.assertEqual(b"php", (config / "artifacts.json").read_bytes())
                (config / "node-artifacts.json").write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "non-fixture"):
                    fixture.unstage(directory)
                self.assertTrue((config / "artifacts.json").exists())
                (config / "node-artifacts.json").write_bytes(b"node")
                fixture.unstage(directory)
                self.assertFalse((config / "artifacts.json").exists())
                self.assertFalse((config / "node-artifacts.json").exists())

    def test_seed_refuses_newer_installed_patch_before_mutating(self) -> None:
        with mock.patch.object(fixture, "fixture_payloads", return_value=(b"php", b"node")), \
             mock.patch.object(fixture, "installed", side_effect=["8.5.10", None]), \
             mock.patch.object(fixture, "install_old") as install, \
             mock.patch.object(fixture.os, "geteuid", return_value=1000), \
             mock.patch.object(fixture.os, "uname") as uname:
            uname.return_value.machine = "x86_64"
            with self.assertRaisesRegex(ValueError, "already has 8.5.10"):
                fixture.seed(Path("/tmp/fixture"))
            install.assert_not_called()

    def test_install_cleans_only_its_catalog_override_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary)
            override = config / "artifacts.json"

            def command(*args: str) -> str:
                if args[2] == "catalog":
                    override.write_bytes(Path(args[3]).read_bytes())
                    return "catalog selected"
                raise ValueError("simulated install failure")

            with mock.patch.object(fixture, "config_directory", return_value=config), \
                 mock.patch.object(fixture, "installed", return_value=None), \
                 mock.patch.object(fixture, "run", side_effect=command):
                with self.assertRaisesRegex(ValueError, "simulated install failure"):
                    fixture.install_old("php", "8.5", "8.5.8", b"fixture", "artifacts.json")
            self.assertFalse(override.exists())


if __name__ == "__main__":
    unittest.main()
