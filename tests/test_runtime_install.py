from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from paddock.artifacts import ArtifactManifest, ManifestError
from paddock.paths import Paths
from paddock.php_runtime import RuntimeInstaller, RuntimeInstallError
from paddock.state import StateStore


class FakeRuntimeRunner:
    def __init__(self, version: str = "8.4.23", missing: str | None = None):
        self.version = version
        self.missing = missing

    def __call__(self, command, **kwargs):
        if "-v" in command:
            return subprocess.CompletedProcess(command, 0, f"PHP {self.version} (fpm-fcgi)\n", "")
        if "echo PHP_VERSION" in command[-1]:
            return subprocess.CompletedProcess(command, 0, self.version, "")
        if self.missing and f"'{self.missing}'" in command[-1]:
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
                "XDG_RUNTIME_DIR": str(self.base / "runtime"),
            }
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
    def _make_archive(path: Path) -> None:
        with tarfile.open(path, "w:gz") as archive:
            for name in ("runtime/bin/php", "runtime/bin/php-fpm"):
                info = tarfile.TarInfo(name)
                info.mode = 0o755
                content = b"fake runtime"
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
        installer = RuntimeInstaller(self.store, FakeRuntimeRunner())
        destination = installer.install(
            "8.4", ArtifactManifest.load(self.manifest_path)
        )
        active = self.store.paths.data / "runtimes" / "active" / "8.4"
        self.assertEqual(active.resolve(), destination)
        record = self.store.read("runtimes")["runtimes"]["8.4"]
        self.assertEqual(record["sha256"], self.digest)
        config = self.store.paths.state / "fpm" / "php-8.4.conf"
        self.assertIn("clear_env = yes", config.read_text(encoding="utf-8"))
        self.assertIn("fpm.sock", config.read_text(encoding="utf-8"))
        self.assertTrue((self.store.paths.runtime / "php" / "8.4").is_dir())
        self.assertTrue((self.store.paths.state / "logs" / "php" / "8.4").is_dir())

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

    def test_remove_refuses_runtime_used_by_site(self) -> None:
        installer = RuntimeInstaller(self.store, FakeRuntimeRunner())
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
