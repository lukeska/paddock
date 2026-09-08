from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from paddock.integration import INSTALL_CHANGES, REMOVE_CHANGES, SYSTEM_HELPER, Integration
from paddock.artifacts import normalized_architecture
from paddock.paths import Paths
from paddock.state import StateStore


class FakeIntegrationCommands:
    def __init__(self, caroot: Path):
        self.caroot = caroot
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        if command[0] == "mkcert":
            self.caroot.mkdir(parents=True, exist_ok=True)
            (self.caroot / "rootCA.pem").write_text("root", encoding="utf-8")
            (self.caroot / "rootCA-key.pem").write_text("key", encoding="utf-8")
            Path(command[2]).write_text("leaf", encoding="utf-8")
            Path(command[4]).write_text("leaf key", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        paths = Paths.from_environment(
            {
                "HOME": str(base / "home"),
                "XDG_CONFIG_HOME": str(base / "config"),
                "XDG_DATA_HOME": str(base / "data"),
                "XDG_STATE_HOME": str(base / "state"),
                "XDG_CACHE_HOME": str(base / "cache"),
            },
            runtime_root=base / "run" / "paddock",
        )
        self.store = StateStore(paths)
        self.store.initialize()
        self.fake = FakeIntegrationCommands(paths.data / "pki")

    def tearDown(self):
        self.temporary.cleanup()

    def test_prepare_creates_ca_and_empty_validated_projection(self):
        integration = Integration(self.store, self.fake)
        integration.prepare()
        self.assertTrue((self.store.paths.data / "pki/rootCA.pem").is_file())
        self.assertFalse((self.store.paths.data / "pki/.bootstrap-key.pem").exists())
        promoted = self.store.paths.state / "nginx/current"
        self.assertTrue((promoted / "nginx.conf").is_file())
        self.assertTrue((promoted / "sites/000-default.conf").is_file())
        self.assertTrue((promoted / "snippets/php-fastcgi.conf").is_file())
        # nginx will not create a log directory, and a missing one is a
        # startup failure rather than something it recovers from.
        self.assertTrue((self.store.paths.state / "logs/sites").is_dir())
        self.assertTrue((self.store.paths.home / "Paddock").is_dir())
        self.assertEqual(
            [self.store.paths.home / "Paddock"],
            [Path(path) for path in self.store.read("parking")["paths"]],
        )

    def test_prepare_replaces_a_stale_projection(self):
        # A tree generated before the socket layout changed must not survive
        # setup, or nginx dials a socket no unit binds.
        stale = self.store.paths.state / "nginx/generations/00000001"
        (stale / "sites").mkdir(parents=True, exist_ok=True)
        (stale / "nginx.conf").write_text("stale", encoding="utf-8")
        (stale / "sites/ghost.conf").write_text(
            "stale unix//run/user/1000/paddock", encoding="utf-8"
        )
        current = self.store.paths.state / "nginx/current"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.symlink_to(stale, target_is_directory=True)
        Integration(self.store, self.fake).prepare()
        self.assertNotEqual(stale, current.resolve())
        self.assertFalse((current / "sites/ghost.conf").exists())
        self.assertNotIn("stale", (current / "nginx.conf").read_text(encoding="utf-8"))

    def test_prepare_removes_state_from_the_previous_web_server(self):
        # Removed only after the nginx tree is promoted, so a failed
        # projection leaves a machine able to roll back to the old package.
        for legacy in ("caddy", "caddy-data", "caddy-config"):
            directory = self.store.paths.state / legacy
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "leftover").write_text("x", encoding="utf-8")
        Integration(self.store, self.fake).prepare()
        for legacy in ("caddy", "caddy-data", "caddy-config"):
            self.assertFalse((self.store.paths.state / legacy).exists())

    def test_helper_invocations_are_fixed_and_user_scoped(self):
        integration = Integration(self.store, self.fake)
        with patch.dict("os.environ", {"USER": "demo"}, clear=False):
            integration.install()
            integration.uninstall()
        helpers = [call for call in self.fake.calls if call[:2] == ["sudo", str(SYSTEM_HELPER)]]
        install, uninstall = helpers
        self.assertEqual(install[2:5], ["install", "--user", "demo"])
        self.assertEqual(uninstall[2:5], ["uninstall", "--user", "demo"])
        self.assertIn(str(self.store.paths.data), install)
        self.assertIn(str(self.store.paths.state), install)

    def test_change_previews_are_explicit(self):
        self.assertTrue(any("~test" in change for change in INSTALL_CHANGES))
        self.assertTrue(any("latest published PHP" in change for change in INSTALL_CHANGES))
        self.assertTrue(any("preserve projects" in change for change in REMOVE_CHANGES))

    def test_first_setup_installs_latest_php_once_and_makes_it_default(self):
        manifest = Path(self.temporary.name) / "artifacts.json"
        architecture = normalized_architecture()
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "artifacts": [
                {
                    "php": "8.4.23", "minor": "8.4", "architecture": architecture,
                    "url": "https://example.test/php-8.4.tar.gz", "sha256": "1" * 64,
                },
                {
                    "php": "8.5.8", "minor": "8.5", "architecture": architecture,
                    "url": "https://example.test/php-8.5.tar.gz", "sha256": "2" * 64,
                },
            ],
        }), encoding="utf-8")
        integration = Integration(
            self.store, self.fake, artifact_paths=(manifest,)
        )
        with patch("paddock.integration.RuntimeInstaller.install", autospec=True) as install:
            self.assertEqual("8.5", integration.install_initial_php())
            self.assertIsNone(integration.install_initial_php())

        self.assertEqual(1, install.call_count)
        self.assertEqual("8.5", install.call_args.args[1])
        settings = self.store.read("settings")
        self.assertEqual("8.5", settings["default_php"])
        self.assertTrue(settings["initial_php_setup_complete"])

    def test_failed_initial_php_install_remains_retryable(self):
        integration = Integration(
            self.store,
            self.fake,
            artifact_paths=(Path(self.temporary.name) / "missing.json",),
        )
        with self.assertRaisesRegex(RuntimeError, "runtime catalog"):
            integration.install_initial_php()
        self.assertFalse(
            self.store.read("settings")["initial_php_setup_complete"]
        )
