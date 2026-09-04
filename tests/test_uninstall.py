from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from paddock.paths import Paths
from paddock.uninstall import PurgeError, PurgePlan


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")


class PurgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.paths = Paths.from_environment({
            "HOME": str(base / "home"),
            "XDG_CONFIG_HOME": str(base / "config"),
            "XDG_DATA_HOME": str(base / "data"),
            "XDG_STATE_HOME": str(base / "state"),
            "XDG_CACHE_HOME": str(base / "cache"),
        }, runtime_root=base / "run/paddock")
        for root in (self.paths.config, self.paths.data, self.paths.state, self.paths.cache):
            root.mkdir(parents=True)
            (root / "kept-until-purge").write_text("x", encoding="utf-8")
        (self.paths.config / "services-v2.json").write_text(json.dumps({
            "schema_version": 2,
            "instances": {"mysql-deadbeef": {
                "id": "mysql-deadbeef", "type": "mysql", "label": "MySQL",
                "image": "example/mysql:8", "port": 3306,
                "volume": "paddock-mysql-deadbeef",
            }},
        }), encoding="utf-8")
        self.units = self.paths.config.parent / "systemd/user"
        self.units.mkdir(parents=True)
        (self.units / "paddock-service-mysql-deadbeef.service").write_text("unit")
        self.project = base / "project"
        self.project.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_default_purge_preserves_volumes_and_projects(self) -> None:
        plan = PurgePlan.discover(self.paths)
        runner = FakeRunner()
        plan.execute(False, runner)
        self.assertTrue(self.project.is_dir())
        self.assertTrue(all(not path.exists() for path in plan.paths))
        self.assertFalse(any(call[1:3] == ["volume", "rm"] for call in runner.calls))
        self.assertIn("preserve service volume: paddock-mysql-deadbeef", plan.preview(False))

    def test_service_data_requires_the_explicit_execution_option(self) -> None:
        plan = PurgePlan.discover(self.paths)
        runner = FakeRunner()
        plan.execute(True, runner)
        self.assertIn(
            ["podman", "volume", "rm", "--force", "paddock-mysql-deadbeef"],
            runner.calls,
        )

    def test_refuses_a_root_not_named_paddock(self) -> None:
        unsafe = Paths(Path("/tmp/config"), self.paths.data, self.paths.state,
                       self.paths.cache, self.paths.runtime, self.paths.home)
        with self.assertRaisesRegex(PurgeError, "unsafe purge path"):
            PurgePlan.discover(unsafe)
