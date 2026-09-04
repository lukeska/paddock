from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from paddock.caddy import CaddyProjector
from paddock.paths import Paths
from paddock.reverb import ReverbError, ReverbManager, detects_reverb
from paddock.runtimes import RuntimeRegistry
from paddock.sites import SiteManager
from paddock.state import StateStore


class Runner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.active = "inactive"
        self.enabled = False

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        if "is-active" in command:
            return subprocess.CompletedProcess(command, 0, self.active + "\n", "")
        if "is-enabled" in command:
            value = "enabled" if self.enabled else "disabled"
            return subprocess.CompletedProcess(command, 0 if self.enabled else 1, value + "\n", "")
        if command[0] == "journalctl":
            return subprocess.CompletedProcess(command, 0, "ready\nconnected\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")


class ReverbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.paths = Paths.from_environment({
            "HOME": str(root / "home"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_DATA_HOME": str(root / "data"),
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_CACHE_HOME": str(root / "cache"),
        }, runtime_root=root / "run")
        self.store = StateStore(self.paths)
        self.store.initialize()
        php = root / "php-8.4/bin/php"
        php.parent.mkdir(parents=True)
        php.write_text("php", encoding="utf-8")
        php.chmod(0o755)
        RuntimeRegistry(self.store).register("8.4", php)
        self.store.update("settings", lambda value: {**value, "default_php": "8.4"})
        self.runner = Runner()
        self.root = root / "demo"
        (self.root / "public").mkdir(parents=True)
        (self.root / "artisan").write_text("artisan", encoding="utf-8")
        self.projector = CaddyProjector(self.paths, self.runner)
        SiteManager(self.store, self.projector).link(self.root, "demo", reload=False)
        self.manager = ReverbManager(
            self.store, self.runner,
            port_available=lambda _host, port: port != 8080,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def install_reverb(self) -> None:
        (self.root / "composer.json").write_text(json.dumps({
            "require": {"laravel/reverb": "^1.0"},
        }), encoding="utf-8")
        (self.root / ".env").write_text("APP_NAME=Demo\n", encoding="utf-8")

    def test_detection_uses_composer_or_broadcast_configuration(self) -> None:
        self.assertFalse(detects_reverb(self.root))
        self.install_reverb()
        self.assertTrue(detects_reverb(self.root))
        (self.root / "composer.json").unlink()
        (self.root / ".env").write_text(
            "BROADCAST_CONNECTION=reverb\n", encoding="utf-8"
        )
        self.assertTrue(detects_reverb(self.root))

    def test_configure_allocates_port_projects_unit_env_and_caddy(self) -> None:
        self.install_reverb()
        worker = self.manager.configure("demo")
        self.assertEqual(8081, worker.port)
        unit = (self.manager.unit_directory / worker.unit).read_text(encoding="utf-8")
        self.assertIn(str(self.root / "artisan"), unit)
        self.assertIn("reverb:start", unit)
        self.assertIn("--host=127.0.0.1", unit)
        self.assertIn("--port=8081", unit)
        self.assertIn(str(self.root.parent / "php-8.4/bin/php"), unit)
        env = (self.root / ".env").read_text(encoding="utf-8")
        self.assertIn("REVERB_SERVER_HOST=127.0.0.1", env)
        self.assertIn("REVERB_SERVER_PORT=8081", env)
        self.assertIn("REVERB_HOST=demo.test", env)
        self.assertIn("REVERB_PORT=80", env)
        self.assertIn("REVERB_SCHEME=http", env)
        caddy = self.projector.path.read_text(encoding="utf-8")
        self.assertIn("@reverb path /app/* /apps/*", caddy)
        self.assertIn("reverse_proxy @reverb 127.0.0.1:8081", caddy)

    def test_start_configures_and_controls_site_worker(self) -> None:
        self.install_reverb()
        worker = self.manager.control("start", "demo")
        self.assertIn(["systemctl", "--user", "start", worker.unit], self.runner.calls)
        self.assertEqual(("ready", "connected"), self.manager.logs("demo"))

    def test_unavailable_project_is_refused(self) -> None:
        with self.assertRaisesRegex(ReverbError, "does not have laravel/reverb"):
            self.manager.configure("demo")


if __name__ == "__main__":
    unittest.main()
