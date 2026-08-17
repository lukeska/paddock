from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from paddock.caddy import CaddyProjector
from paddock.diagnostics import doctor, service_status
from paddock.lifecycle import Lifecycle, LifecycleError
from paddock.paths import Paths
from paddock.runtimes import RuntimeRegistry
from paddock.state import StateStore
from paddock.tls import SecurityManager, TlsError


class FakeCommands:
    def __init__(self):
        self.fail_issue = False
        self.fail_validate = False
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        if command[0] == "mkcert":
            if self.fail_issue:
                return subprocess.CompletedProcess(command, 1, "", "issuance rejected")
            Path(command[2]).write_text("certificate", encoding="utf-8")
            Path(command[4]).write_text("private key", encoding="utf-8")
        failed = self.fail_validate and "validate" in command
        return subprocess.CompletedProcess(command, 1 if failed else 0, "active\n", "bad" if failed else "")


class TlsDiagnosticTests(unittest.TestCase):
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
                "XDG_RUNTIME_DIR": str(base / "runtime"),
            }
        )
        self.store = StateStore(paths)
        self.store.initialize()
        php = base / "php" / "bin" / "php"
        php.parent.mkdir(parents=True)
        php.write_text("php", encoding="utf-8")
        php.chmod(0o755)
        RuntimeRegistry(self.store).register("8.4", php)
        project = base / "project"
        (project / "public").mkdir(parents=True)
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
        self.fake = FakeCommands()
        self.projector = CaddyProjector(paths, self.fake)
        self.projector.write(self.projector.render(self.store.read("sites")["sites"]))
        self.security = SecurityManager(self.store, self.projector, self.fake)

    def tearDown(self):
        self.temporary.cleanup()

    def test_secure_issues_exact_and_wildcard_and_updates_projection(self):
        self.security.secure("demo", reload=False)
        record = self.store.read("sites")["sites"]["demo"]
        self.assertTrue(record["secured"])
        self.assertIn("https://demo.test", self.projector.path.read_text(encoding="utf-8"))
        mkcert = self.fake.calls[0]
        self.assertEqual(mkcert[-2:], ["demo.test", "*.demo.test"])
        key = self.store.paths.data / "pki/sites/demo/private-key.pem"
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)

    def test_failed_issuance_or_validation_preserves_unsecured_state(self):
        self.fake.fail_issue = True
        with self.assertRaises(TlsError):
            self.security.secure("demo", reload=False)
        self.assertFalse(self.store.read("sites")["sites"]["demo"]["secured"])
        self.fake.fail_issue = False
        self.fake.fail_validate = True
        with self.assertRaises(Exception):
            self.security.secure("demo", reload=False)
        self.assertFalse(self.store.read("sites")["sites"]["demo"]["secured"])
        self.assertFalse((self.store.paths.data / "pki/sites/demo/private-key.pem").exists())

    def test_unsecure_removes_leaf_only_after_activation(self):
        self.security.secure("demo", reload=False)
        self.security.unsecure("demo", reload=False)
        self.assertFalse(self.store.read("sites")["sites"]["demo"]["secured"])
        self.assertFalse((self.store.paths.data / "pki/sites/demo").exists())

    def test_doctor_reports_state_runtime_site_and_caddy(self):
        checks = doctor(self.store, self.fake)
        names = {check.name: check.ok for check in checks}
        self.assertTrue(names["state:sites"])
        self.assertTrue(names["php:8.4"])
        self.assertTrue(names["site:demo"])
        self.assertTrue(names["caddy:config"])

    def test_status_and_lifecycle_use_fixed_units(self):
        statuses = service_status(self.fake)
        self.assertTrue(all(status.ok for status in statuses))
        Lifecycle(self.fake).control("restart")
        self.assertIn(["systemctl", "restart", "paddock.target"], self.fake.calls)
        with self.assertRaises(LifecycleError):
            Lifecycle(self.fake).control("reload")
