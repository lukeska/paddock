from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from paddock.config_watcher import ConfigWatcher
from paddock.paths import Paths
from paddock.state import StateStore


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, command, **_kwargs):
        self.calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")


class Manager:
    def __init__(self, error=None):
        self.calls = 0
        self.error = error

    def reproject(self):
        self.calls += 1
        if self.error:
            raise self.error


class ConfigWatcherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = StateStore(Paths(
            config=root / "config/paddock", data=root / "data/paddock",
            state=root / "state/paddock", cache=root / "cache/paddock",
            runtime=root / "run/paddock", home=root / "home",
        ))
        self.store.initialize()
        self.runner = Runner()
        self.watcher = ConfigWatcher(self.store, self.runner)

    def tearDown(self):
        self.temporary.cleanup()

    def test_install_writes_and_enables_a_user_service(self):
        path = self.watcher.install()
        unit = path.read_text(encoding="utf-8")
        self.assertIn("python -m paddock.config_watcher", unit)
        self.assertIn("WantedBy=default.target", unit)
        self.assertIn(
            ["systemctl", "--user", "enable", "--now", path.name], self.runner.calls
        )

    def test_a_saved_custom_fragment_triggers_one_reprojection(self):
        previous = self.watcher.fingerprint()
        fragment = self.store.paths.config / "nginx/app.custom.conf"
        fragment.parent.mkdir(parents=True)
        fragment.write_text("client_max_body_size 1g;\n", encoding="utf-8")
        manager = Manager()
        current, changed = self.watcher.reconcile_if_changed(
            previous, manager, debounce=0, sleeper=lambda _delay: None
        )
        self.assertTrue(changed)
        self.assertNotEqual(previous, current)
        self.assertEqual(1, manager.calls)
        current, changed = self.watcher.reconcile_if_changed(
            current, manager, debounce=0, sleeper=lambda _delay: None
        )
        self.assertFalse(changed)
        self.assertEqual(1, manager.calls)

    def test_declared_project_fragment_is_part_of_the_fingerprint(self):
        project = self.store.paths.home / "app"
        project.mkdir(parents=True)
        fragment = project / "nginx.conf"
        fragment.write_text("# first\n", encoding="utf-8")
        self.store.write("sites", {"schema_version": 1, "sites": {"app": {
            "name": "app", "root": str(project), "php": "8.5", "secured": False,
            "nginx": {"path": "nginx.conf", "sha256": "0" * 64},
        }}})
        before = self.watcher.fingerprint()
        fragment.write_text("# changed\n", encoding="utf-8")
        self.assertNotEqual(before, self.watcher.fingerprint())

    def test_rejected_fragment_is_recorded_until_a_valid_save(self):
        previous_sources = self.watcher.sources()
        previous = self.watcher.fingerprint()
        fragment = self.store.paths.config / "nginx/app.custom.conf"
        fragment.parent.mkdir(parents=True)
        fragment.write_text("broken on;\n", encoding="utf-8")

        current, changed, current_sources = self.watcher.reconcile_if_changed(
            previous, Manager(RuntimeError("nginx rejected it")), debounce=0,
            sleeper=lambda _delay: None, previous_sources=previous_sources,
        )
        self.assertTrue(changed)
        status = self.watcher.status_path.read_text(encoding="utf-8")
        self.assertIn(str(fragment), status)
        self.assertIn("nginx rejected it", status)

        fragment.write_text("client_max_body_size 1g;\n", encoding="utf-8")
        self.watcher.reconcile_if_changed(
            current, Manager(), debounce=0, sleeper=lambda _delay: None,
            previous_sources=current_sources,
        )
        self.assertFalse(self.watcher.status_path.exists())


if __name__ == "__main__":
    unittest.main()
