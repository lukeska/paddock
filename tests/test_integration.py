from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from paddock.integration import INSTALL_CHANGES, REMOVE_CHANGES, Integration
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
        self.assertTrue((self.store.paths.state / "caddy/Caddyfile").is_file())
        self.assertTrue((self.store.paths.state / "caddy-data").is_dir())
        self.assertTrue((self.store.paths.state / "caddy-config").is_dir())

    def test_prepare_replaces_a_stale_projection(self):
        # A Caddyfile generated before the socket layout changed must not
        # survive setup, or Caddy dials a socket no unit binds.
        projection = self.store.paths.state / "caddy/Caddyfile"
        projection.parent.mkdir(parents=True, exist_ok=True)
        projection.write_text("stale unix//run/user/1000/paddock", encoding="utf-8")
        Integration(self.store, self.fake).prepare()
        self.assertNotIn("stale", projection.read_text(encoding="utf-8"))

    def test_helper_invocations_are_fixed_and_user_scoped(self):
        integration = Integration(self.store, self.fake)
        with patch.dict("os.environ", {"USER": "demo"}, clear=False):
            integration.install()
            integration.uninstall()
        install = self.fake.calls[-2]
        uninstall = self.fake.calls[-1]
        self.assertEqual(install[2:5], ["install", "--user", "demo"])
        self.assertEqual(uninstall[2:5], ["uninstall", "--user", "demo"])
        self.assertIn(str(self.store.paths.data), install)
        self.assertIn(str(self.store.paths.state), install)

    def test_change_previews_are_explicit(self):
        self.assertTrue(any("~test" in change for change in INSTALL_CHANGES))
        self.assertTrue(any("preserve projects" in change for change in REMOVE_CHANGES))
