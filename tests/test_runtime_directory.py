"""Fresh-boot regression coverage for the PHP-FPM runtime directory.

Paddock PHP units failed at boot with `226/NAMESPACE` because the generated
unit named `/run/user/<uid>/paddock` in `ReadWritePaths=`. `user-runtime-dir@`
creates `/run/user/<uid>` but never the Paddock child, and systemd builds the
mount namespace before `ExecStart`, so the launcher never ran. These tests pin
the systemd-owned replacement: the runtime directory must be created by
`RuntimeDirectory=` and Paddock itself must never depend on it already
existing.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

from paddock.caddy import CaddyProjector
from paddock.paths import SYSTEM_RUNTIME_ROOT, Paths
from paddock.php_runtime import RuntimeInstaller
from paddock.runtimes import RuntimeRegistry
from paddock.state import StateStore


def load_helper():
    path = Path(__file__).parents[1] / "system/system-helper"
    loader = importlib.machinery.SourceFileLoader("paddock_system_helper", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def directives(unit: str, name: str) -> list[str]:
    prefix = f"{name}="
    return [line[len(prefix):] for line in unit.splitlines() if line.startswith(prefix)]


class PhpUnitRuntimeDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        helper = load_helper()
        self.unit = helper.php_unit(
            "demo", 1000, Path("/home/demo/.local/share/paddock"),
            Path("/home/demo/.local/state/paddock"),
        )

    def test_systemd_creates_the_per_version_runtime_directory(self) -> None:
        self.assertIn("RuntimeDirectory=paddock/php/%i", self.unit)
        self.assertIn("RuntimeDirectoryMode=0700", self.unit)

    def test_unit_does_not_depend_on_a_login_session_runtime_directory(self) -> None:
        self.assertNotIn("/run/user/", self.unit)
        self.assertNotIn("user-runtime-dir@", self.unit)

    def test_no_writable_path_is_declared_below_run(self) -> None:
        # A `ReadWritePaths=` entry that does not exist yet aborts namespace
        # setup with 226/NAMESPACE. Only systemd may hand out /run access.
        for value in directives(self.unit, "ReadWritePaths"):
            for entry in value.split():
                self.assertFalse(
                    entry.startswith("/run"),
                    f"{entry} must be provided by RuntimeDirectory=, not ReadWritePaths=",
                )

    def test_unit_and_cli_agree_on_the_socket_directory(self) -> None:
        # Drift here silently routes Caddy at a socket FPM never binds.
        declared = directives(self.unit, "RuntimeDirectory")
        self.assertEqual(1, len(declared))
        systemd_owned = Path("/run") / declared[0].replace("%i", "8.4")
        self.assertEqual(SYSTEM_RUNTIME_ROOT / "php" / "8.4", systemd_owned)


class AbsentRuntimeRootTests(unittest.TestCase):
    """Everything Paddock generates must work on a boot where /run is bare."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.runtime_root = base / "run" / "paddock"
        self.paths = Paths.from_environment(
            {
                "HOME": str(base / "home"),
                "XDG_CONFIG_HOME": str(base / "config"),
                "XDG_DATA_HOME": str(base / "data"),
                "XDG_STATE_HOME": str(base / "state"),
                "XDG_CACHE_HOME": str(base / "cache"),
            },
            runtime_root=self.runtime_root,
        )
        self.store = StateStore(self.paths)
        self.store.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_initialize_does_not_create_the_systemd_owned_root(self) -> None:
        self.assertFalse(self.runtime_root.exists())

    def test_caddy_projection_targets_the_systemd_owned_socket(self) -> None:
        rendered = CaddyProjector(self.paths).render(
            {"demo": {"root": str(Path(self.temporary.name) / "demo"), "php": "8.4", "secured": False}}
        )
        self.assertIn(f"unix/{self.runtime_root / 'php' / '8.4' / 'fpm.sock'}", rendered)
        self.assertFalse(self.runtime_root.exists())

    def test_fpm_configuration_is_written_without_the_socket_directory(self) -> None:
        installer = RuntimeInstaller(
            self.store,
            runner=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
        )
        runtime = Path(self.temporary.name) / "runtime"
        (runtime / "bin").mkdir(parents=True)
        installer._write_fpm_config("8.4", runtime)

        written = (self.paths.state / "fpm" / "php-8.4.conf").read_text(encoding="utf-8")
        self.assertIn(f"listen = {self.runtime_root / 'php' / '8.4' / 'fpm.sock'}", written)
        self.assertIn(f"pid = {self.runtime_root / 'php' / '8.4' / 'php-fpm.pid'}", written)
        self.assertFalse(self.runtime_root.exists())


class ReprojectionTests(unittest.TestCase):
    """An FPM config written before the socket layout changed must not survive."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.runtime_root = base / "run" / "paddock"
        self.paths = Paths.from_environment(
            {"HOME": str(base / "home"), "XDG_STATE_HOME": str(base / "state"),
             "XDG_DATA_HOME": str(base / "data"), "XDG_CONFIG_HOME": str(base / "config"),
             "XDG_CACHE_HOME": str(base / "cache")},
            runtime_root=self.runtime_root,
        )
        self.store = StateStore(self.paths)
        self.store.initialize()
        self.active = self.paths.data / "runtimes" / "active" / "8.4"
        (self.active / "bin").mkdir(parents=True)
        php = self.active / "bin" / "php"
        php.write_text("#!/bin/sh\n", encoding="utf-8")
        php.chmod(0o755)
        (self.active / "bin" / "php-fpm").write_text("#!/bin/sh\n", encoding="utf-8")
        RuntimeRegistry(self.store).register("8.4", php, "0" * 64)
        self.config = self.paths.state / "fpm" / "php-8.4.conf"
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(
            "[paddock]\nlisten = /run/user/1000/paddock/php/8.4/fpm.sock\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_reproject_rewrites_a_session_scoped_socket(self) -> None:
        installer = RuntimeInstaller(
            self.store,
            runner=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
        )
        self.assertEqual(["8.4"], installer.reproject())
        written = self.config.read_text(encoding="utf-8")
        self.assertNotIn("/run/user/", written)
        self.assertIn(f"listen = {self.runtime_root / 'php' / '8.4' / 'fpm.sock'}", written)
        self.assertFalse(self.runtime_root.exists())

    def test_reproject_skips_a_registered_runtime_without_fpm(self) -> None:
        (self.active / "bin" / "php-fpm").unlink()
        installer = RuntimeInstaller(
            self.store,
            runner=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
        )
        self.assertEqual([], installer.reproject())


if __name__ == "__main__":
    unittest.main()
