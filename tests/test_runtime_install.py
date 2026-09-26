from __future__ import annotations

import hashlib
import grp
import io
import json
import os
from pathlib import Path
import pwd
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from paddock.artifacts import ArtifactManifest, ManifestError
from paddock.paths import Paths
from paddock.php_runtime import RuntimeInstaller, RuntimeInstallError
from paddock.state import StateStore


class FakeRuntimeRunner:
    def __init__(
        self,
        version: str = "8.4.23",
        missing: str | None = None,
        missing_function: str | None = None,
    ):
        self.version = version
        self.missing = missing
        self.missing_function = missing_function
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        if "-v" in command:
            return subprocess.CompletedProcess(command, 0, f"PHP {self.version} (fpm-fcgi)\n", "")
        if "echo PHP_VERSION" in command[-1]:
            return subprocess.CompletedProcess(command, 0, self.version, "")
        if self.missing and f"'{self.missing}'" in command[-1]:
            return subprocess.CompletedProcess(command, 1, "", "missing")
        if self.missing_function and f"'{self.missing_function}'" in command[-1]:
            return subprocess.CompletedProcess(command, 1, "", "missing")
        return subprocess.CompletedProcess(command, 0, "", "")


class RuntimeInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        paths = Paths.from_environment(
            {
                "HOME": str(self.base / "home"),
                "XDG_CONFIG_HOME": str(self.base / "config"),
                "XDG_DATA_HOME": str(self.base / "data"),
                "XDG_STATE_HOME": str(self.base / "state"),
                "XDG_CACHE_HOME": str(self.base / "cache"),
            },
            runtime_root=self.base / "run" / "paddock",
        )
        self.store = StateStore(paths)
        self.store.initialize()
        self.archive = self.base / "php.tar.gz"
        self._make_archive(self.archive)
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.manifest_path = self.base / "manifest.json"
        self._write_manifest(self.digest)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _make_archive(path: Path, version: str = "8.4.23") -> None:
        with tarfile.open(path, "w:gz") as archive:
            for name in ("runtime/bin/php", "runtime/bin/php-fpm"):
                info = tarfile.TarInfo(name)
                info.mode = 0o755
                content = f"fake runtime {version}".encode()
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))

    def _write_manifest(self, digest: str) -> None:
        self.manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "artifacts": [
                        {
                            "php": "8.4.23",
                            "minor": "8.4",
                            "architecture": "x86_64",
                            "url": self.archive.as_uri(),
                            "sha256": digest,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_verified_install_activates_registers_and_projects_fpm(self) -> None:
        runner = FakeRuntimeRunner()
        installer = RuntimeInstaller(self.store, runner)
        destination = installer.install(
            "8.4", ArtifactManifest.load(self.manifest_path)
        )
        active = self.store.paths.data / "runtimes" / "active" / "8.4"
        self.assertEqual(active.resolve(), destination)
        record = self.store.read("runtimes")["runtimes"]["8.4"]
        self.assertEqual(record["sha256"], self.digest)
        self.assertEqual("8.4.23", record["release"])
        config = self.store.paths.state / "fpm" / "php-8.4.conf"
        rendered = config.read_text(encoding="utf-8")
        self.assertIn("clear_env = yes", rendered)
        self.assertIn("fpm.sock", rendered)
        self.assertIn(f"user = {pwd.getpwuid(os.getuid()).pw_name}", rendered)
        self.assertIn(f"group = {grp.getgrgid(os.getgid()).gr_name}", rendered)
        self.assertIn(
            f"listen = {self.store.paths.runtime / 'php' / '8.4' / 'fpm.sock'}",
            config.read_text(encoding="utf-8"),
        )
        # The socket directory belongs to the unit's RuntimeDirectory=.
        self.assertFalse((self.store.paths.runtime / "php" / "8.4").exists())
        self.assertTrue((self.store.paths.state / "logs" / "php" / "8.4").is_dir())
        self.assertIn(
            ["systemctl", "restart", "paddock-php@8.4.service"], runner.calls
        )

    def test_checksum_mismatch_never_activates(self) -> None:
        self._write_manifest("0" * 64)
        installer = RuntimeInstaller(self.store, FakeRuntimeRunner())
        with self.assertRaisesRegex(RuntimeInstallError, "checksum mismatch"):
            installer.install("8.4", ArtifactManifest.load(self.manifest_path))
        self.assertEqual(self.store.read("runtimes")["runtimes"], {})

    def test_missing_baseline_extension_never_activates(self) -> None:
        installer = RuntimeInstaller(self.store, FakeRuntimeRunner(missing="intl"))
        with self.assertRaisesRegex(RuntimeInstallError, "intl"):
            installer.install("8.4", ArtifactManifest.load(self.manifest_path))
        self.assertEqual(self.store.read("runtimes")["runtimes"], {})

    def test_mbstring_without_mbregex_never_activates(self) -> None:
        installer = RuntimeInstaller(
            self.store, FakeRuntimeRunner(missing_function="mb_split")
        )
        with self.assertRaisesRegex(RuntimeInstallError, "mb_split"):
            installer.install("8.4", ArtifactManifest.load(self.manifest_path))
        self.assertEqual(self.store.read("runtimes")["runtimes"], {})

    def test_remove_refuses_runtime_used_by_site(self) -> None:
        runner = FakeRuntimeRunner()
        installer = RuntimeInstaller(self.store, runner)
        installer.install("8.4", ArtifactManifest.load(self.manifest_path))
        project = self.base / "app"
        project.mkdir()
        self.store.write(
            "sites",
            {
                "schema_version": 1,
                "sites": {
                    "demo": {
                        "name": "demo", "root": str(project), "php": "8.4", "secured": False
                    }
                },
            },
        )
        with self.assertRaisesRegex(RuntimeInstallError, "demo.test"):
            installer.remove("8.4")
        self.assertNotIn(
            ["systemctl", "stop", "paddock-php@8.4.service"], runner.calls
        )

    def test_remove_stops_service_before_discarding_runtime(self) -> None:
        runner = FakeRuntimeRunner()
        installer = RuntimeInstaller(self.store, runner)
        installer.install("8.4", ArtifactManifest.load(self.manifest_path))
        installer.remove("8.4")
        self.assertIn(
            ["systemctl", "stop", "paddock-php@8.4.service"], runner.calls
        )
        self.assertEqual(self.store.read("runtimes")["runtimes"], {})

    def test_manifest_selects_highest_patch_and_rejects_bad_shape(self) -> None:
        value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        newer = dict(value["artifacts"][0], php="8.4.24")
        value["artifacts"].append(newer)
        self.manifest_path.write_text(json.dumps(value), encoding="utf-8")
        self.assertEqual(
            ArtifactManifest.load(self.manifest_path).select("8.4", "x86_64").php,
            "8.4.24",
        )
        value["unknown"] = True
        self.manifest_path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ManifestError):
            ArtifactManifest.load(self.manifest_path)

    def test_archive_links_are_rejected(self) -> None:
        with tarfile.open(self.archive, "w:gz") as archive:
            info = tarfile.TarInfo("runtime/bin/php")
            info.type = tarfile.SYMTYPE
            info.linkname = "/bin/sh"
            archive.addfile(info)
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self._write_manifest(self.digest)
        with self.assertRaisesRegex(RuntimeInstallError, "unsafe archive"):
            RuntimeInstaller(self.store, FakeRuntimeRunner()).install(
                "8.4", ArtifactManifest.load(self.manifest_path)
            )

    def _candidate(self, version: str) -> ArtifactManifest:
        archive = self.base / f"php-{version}.tar.gz"
        self._make_archive(archive, version)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest = self.base / f"manifest-{version}.json"
        manifest.write_text(json.dumps({"schema_version": 1, "artifacts": [{
            "php": version, "minor": "8.4", "architecture": "x86_64",
            "url": archive.as_uri(), "sha256": digest,
        }]}), encoding="utf-8")
        return ArtifactManifest.load(manifest)

    def test_patch_update_noop_downgrade_and_rollback(self) -> None:
        class VersionedRunner(FakeRuntimeRunner):
            def __call__(self, command, **kwargs):
                if command[0] != "systemctl":
                    self.version = "8.4.24" if "8.4.24" in Path(command[0]).read_text() else "8.4.23"
                return super().__call__(command, **kwargs)

        runner = VersionedRunner()
        installer = RuntimeInstaller(self.store, runner)
        old = installer.install("8.4", ArtifactManifest.load(self.manifest_path))
        calls = len(runner.calls)
        self.assertEqual(old, installer.install("8.4", ArtifactManifest.load(self.manifest_path)))
        self.assertEqual(calls, len(runner.calls))
        new = installer.install("8.4", self._candidate("8.4.24"))
        self.assertEqual("8.4.23", installer.registry.resolve("8.4").previous_release)
        with self.assertRaisesRegex(RuntimeInstallError, "older than installed"):
            installer.install("8.4", ArtifactManifest.load(self.manifest_path))
        self.assertEqual(old, installer.rollback("8.4"))
        self.assertEqual("8.4.24", installer.registry.resolve("8.4").previous_release)
        self.assertEqual(old, (self.store.paths.data / "runtimes/active/8.4").resolve())
        self.assertTrue(new.is_dir())

    def test_failed_restart_restores_link_config_and_registry(self) -> None:
        class FailingRunner(FakeRuntimeRunner):
            def __init__(self):
                super().__init__()
                self.fail_once = False

            def __call__(self, command, **kwargs):
                if command[0] != "systemctl":
                    self.version = "8.4.24" if "8.4.24" in Path(command[0]).read_text() else "8.4.23"
                if self.fail_once and command[:2] == ["systemctl", "restart"]:
                    self.fail_once = False
                    self.calls.append(command)
                    return subprocess.CompletedProcess(command, 1, "", "failed")
                return super().__call__(command, **kwargs)

        runner = FailingRunner()
        installer = RuntimeInstaller(self.store, runner)
        old = installer.install("8.4", ArtifactManifest.load(self.manifest_path))
        registry = self.store.read("runtimes")
        config = (self.store.paths.state / "fpm/php-8.4.conf").read_bytes()
        runner.fail_once = True
        with self.assertRaisesRegex(RuntimeInstallError, "activation failed"):
            installer.install("8.4", self._candidate("8.4.24"))
        self.assertEqual(old, (self.store.paths.data / "runtimes/active/8.4").resolve())
        self.assertEqual(registry, self.store.read("runtimes"))
        self.assertEqual(config, (self.store.paths.state / "fpm/php-8.4.conf").read_bytes())
        self.assertEqual(3, sum(c[:2] == ["systemctl", "restart"] for c in runner.calls))

    def test_registry_failure_restores_previous_php(self) -> None:
        class VersionedRunner(FakeRuntimeRunner):
            def __call__(self, command, **kwargs):
                if command[0] != "systemctl":
                    self.version = "8.4.24" if "8.4.24" in Path(command[0]).read_text() else "8.4.23"
                return super().__call__(command, **kwargs)

        installer = RuntimeInstaller(self.store, VersionedRunner())
        old = installer.install("8.4", ArtifactManifest.load(self.manifest_path))
        registry = self.store.read("runtimes")
        with patch.object(installer.registry, "register", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(RuntimeInstallError, "disk full"):
                installer.install("8.4", self._candidate("8.4.24"))
        self.assertEqual(old, (self.store.paths.data / "runtimes/active/8.4").resolve())
        self.assertEqual(registry, self.store.read("runtimes"))

    def test_invalid_candidate_config_restores_old_php_without_restart(self) -> None:
        class VersionedRunner(FakeRuntimeRunner):
            def __call__(self, command, **kwargs):
                if command[0] != "systemctl":
                    self.version = "8.4.24" if "8.4.24" in Path(command[0]).read_text() else "8.4.23"
                if "-t" in command and self.version == "8.4.24":
                    self.calls.append(command)
                    return subprocess.CompletedProcess(command, 1, "", "bad config")
                return super().__call__(command, **kwargs)

        runner = VersionedRunner()
        installer = RuntimeInstaller(self.store, runner)
        old = installer.install("8.4", ArtifactManifest.load(self.manifest_path))
        config = (self.store.paths.state / "fpm/php-8.4.conf").read_bytes()
        calls = len([c for c in runner.calls if c[:2] == ["systemctl", "restart"]])
        with self.assertRaisesRegex(RuntimeInstallError, "bad config"):
            installer.install("8.4", self._candidate("8.4.24"))
        self.assertEqual(old, (self.store.paths.data / "runtimes/active/8.4").resolve())
        self.assertEqual(config, (self.store.paths.state / "fpm/php-8.4.conf").read_bytes())
        self.assertEqual(calls, len([c for c in runner.calls if c[:2] == ["systemctl", "restart"]]))

    def test_custom_php_requires_explicit_replacement(self) -> None:
        custom = self.base / "custom/php"
        custom.parent.mkdir(parents=True)
        custom.write_text("custom", encoding="utf-8")
        custom.chmod(0o755)
        RuntimeInstaller(self.store, FakeRuntimeRunner()).registry.register("8.4", custom)
        installer = RuntimeInstaller(self.store, FakeRuntimeRunner())
        with self.assertRaisesRegex(RuntimeInstallError, "replace-custom"):
            installer.install("8.4", ArtifactManifest.load(self.manifest_path))
        self.assertEqual(custom, installer.registry.resolve("8.4").path)
        installer.install("8.4", ArtifactManifest.load(self.manifest_path), replace_custom=True)
        self.assertIsNone(installer.registry.resolve("8.4").previous_release)

    def test_activation_refuses_regular_file_without_overwriting_it(self) -> None:
        link = self.store.paths.data / "runtimes/active/8.4"
        link.parent.mkdir(parents=True)
        link.write_text("user file", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeInstallError, "not a symlink"):
            RuntimeInstaller(self.store, FakeRuntimeRunner()).install(
                "8.4", ArtifactManifest.load(self.manifest_path)
            )
        self.assertEqual("user file", link.read_text(encoding="utf-8"))

    def test_confirmed_php_patch_must_still_match_catalog(self) -> None:
        installer = RuntimeInstaller(self.store, FakeRuntimeRunner())
        with self.assertRaisesRegex(RuntimeInstallError, "confirm again"):
            installer.install("8.4", ArtifactManifest.load(self.manifest_path),
                              expected_release="8.4.22")
        self.assertEqual({}, self.store.read("runtimes")["runtimes"])
