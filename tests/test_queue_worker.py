from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from paddock.web import WebProjector
from paddock.paths import Paths
from paddock.queue_worker import QueueWorkerError, QueueWorkerManager, detects_laravel
from paddock.runtimes import RuntimeRegistry
from paddock.sites import SiteManager
from paddock.state import StateStore


class Runner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        if "is-active" in command:
            return subprocess.CompletedProcess(command, 0, "inactive\n", "")
        if "is-enabled" in command:
            return subprocess.CompletedProcess(command, 1, "disabled\n", "")
        if command[0] == "journalctl":
            return subprocess.CompletedProcess(command, 0, "processing\ndone\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")


class QueueWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        paths = Paths.from_environment({
            "HOME": str(root / "home"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_DATA_HOME": str(root / "data"),
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_CACHE_HOME": str(root / "cache"),
        }, runtime_root=root / "run")
        self.store = StateStore(paths)
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
        (self.root / "bootstrap").mkdir()
        (self.root / "bootstrap/app.php").write_text("<?php", encoding="utf-8")
        (self.root / "artisan").write_text("artisan", encoding="utf-8")
        (self.root / "composer.json").write_text(json.dumps({
            "require": {"laravel/framework": "^12.0"},
        }), encoding="utf-8")
        SiteManager(self.store, WebProjector(paths, self.runner)).link(
            self.root, "demo", reload=False
        )
        self.manager = QueueWorkerManager(self.store, self.runner)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_detects_laravel_without_exposing_queue_for_plain_php(self) -> None:
        self.assertTrue(detects_laravel(self.root))
        (self.root / "artisan").unlink()
        self.assertFalse(detects_laravel(self.root))

    def test_start_projects_selected_php_queue_worker_and_logs(self) -> None:
        worker = self.manager.control("start", "demo")
        unit = (self.manager.unit_directory / worker.unit).read_text(encoding="utf-8")
        self.assertIn(str(self.root / "artisan"), unit)
        self.assertIn("queue:work", unit)
        self.assertIn("--sleep=1", unit)
        self.assertIn("--tries=3", unit)
        self.assertIn(str(self.root.parent / "php-8.4/bin/php"), unit)
        self.assertIn(["systemctl", "--user", "start", worker.unit], self.runner.calls)
        self.assertEqual(("processing", "done"), self.manager.logs("demo"))

    def test_non_laravel_site_is_refused(self) -> None:
        (self.root / "artisan").unlink()
        with self.assertRaisesRegex(QueueWorkerError, "not a detected Laravel"):
            self.manager.configure("demo")


if __name__ == "__main__":
    unittest.main()
