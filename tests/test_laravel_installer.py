from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from paddock.laravel_installer import (
    ENVIRONMENT_MARKER,
    LaravelInstallerError,
    install_laravel_installer,
    plan_laravel,
    remove_managed_laravel_installer,
)
from paddock.paths import Paths
from paddock.runtimes import RuntimeRegistry
from paddock.state import StateStore


class FakeComposer:
    def __init__(self) -> None:
        self.installed = False
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        composer = command.index(next(item for item in command if item.endswith("composer.phar")))
        arguments = command[composer + 1:]
        if arguments[:3] == ["global", "show", "laravel/installer"]:
            if not self.installed:
                return subprocess.CompletedProcess(command, 1, "", "not installed")
            return subprocess.CompletedProcess(
                command, 0, json.dumps({"versions": ["* v5.32.0"]}), ""
            )
        if arguments[:3] == ["global", "require", "laravel/installer"]:
            self.installed = True
            return subprocess.CompletedProcess(command, 0, "installed", "")
        if arguments[:3] == ["global", "remove", "laravel/installer"]:
            self.installed = False
            return subprocess.CompletedProcess(command, 0, "removed", "")
        return subprocess.CompletedProcess(command, 0, "", "")


class LaravelInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        paths = Paths.from_environment({
            "HOME": str(root / "home"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_DATA_HOME": str(root / "data"),
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_CACHE_HOME": str(root / "cache"),
        })
        self.store = StateStore(paths)
        self.store.initialize()
        self.php = root / "php-8.5/bin/php"
        self.php.parent.mkdir(parents=True)
        self.php.write_text("php", encoding="utf-8")
        self.php.chmod(0o755)
        RuntimeRegistry(self.store).register("8.5", self.php)
        self.store.update("settings", lambda value: {**value, "default_php": "8.5"})
        composer = paths.data / "composer/composer.phar"
        composer.parent.mkdir(parents=True)
        composer.write_text("composer", encoding="utf-8")
        self.fake = FakeComposer()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_installs_missing_global_package_and_records_ownership(self) -> None:
        self.assertEqual("5.32.0", install_laravel_installer(self.store, self.fake))
        self.assertTrue(self.store.read("settings")["laravel_installer_managed"])
        self.assertTrue(any("require" in call for call in self.fake.calls))

    def test_preserves_an_existing_user_installation(self) -> None:
        self.fake.installed = True
        self.assertIsNone(install_laravel_installer(self.store, self.fake))
        self.assertFalse(self.store.read("settings")["laravel_installer_managed"])
        self.assertFalse(any("require" in call for call in self.fake.calls))

    def test_purge_removes_only_an_installer_paddock_installed(self) -> None:
        install_laravel_installer(self.store, self.fake)
        self.assertTrue(remove_managed_laravel_installer(self.store, self.fake))
        self.assertFalse(self.fake.installed)
        self.assertFalse(self.store.read("settings")["laravel_installer_managed"])

    def test_launcher_uses_default_php_and_marks_child_processes(self) -> None:
        global_bin = Path(self.temporary.name) / "global/bin"
        global_bin.mkdir(parents=True)
        laravel = global_bin / "laravel"
        laravel.write_text("#!/usr/bin/env php", encoding="utf-8")

        def locate(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, str(global_bin) + "\n", "")

        plan = plan_laravel(Path(self.temporary.name), ["new", "demo"], self.store, locate)
        self.assertEqual(self.php.resolve(), plan.executable)
        self.assertEqual((str(laravel), "new", "demo"), plan.arguments[-3:])
        self.assertEqual("1", dict(plan.environment)[ENVIRONMENT_MARKER])

    def test_launcher_rejects_an_old_default_before_creating_a_project(self) -> None:
        old = Path(self.temporary.name) / "php-8.1/bin/php"
        old.parent.mkdir(parents=True)
        old.write_text("php", encoding="utf-8")
        old.chmod(0o755)
        RuntimeRegistry(self.store).register("8.1", old)
        self.store.update("settings", lambda value: {**value, "default_php": "8.1"})
        with self.assertRaisesRegex(LaravelInstallerError, "PHP 8.2 or newer"):
            plan_laravel(Path(self.temporary.name), ["new", "demo"], self.store)


if __name__ == "__main__":
    unittest.main()
