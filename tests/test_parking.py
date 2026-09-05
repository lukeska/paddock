from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from paddock.parking import ParkingError, ParkingManager
from paddock.web import WebProjector
from paddock.paths import Paths
from paddock.runtimes import RuntimeRegistry
from paddock.state import StateStore
from support import promoted


class ParkingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.home = root / "home"
        self.home.mkdir()
        self.store = StateStore(Paths(
            config=root / "config/paddock", data=root / "data", state=root / "state",
            cache=root / "cache", runtime=root / "run",
        ))
        self.store.initialize()
        self.calls: list[list[str]] = []

        def runner(command, **_kwargs):
            self.calls.append(list(command))
            return subprocess.CompletedProcess(command, 0, "", "")

        self.manager = ParkingManager(self.store, runner)
        self.projector = WebProjector(self.store.paths, runner)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_default_folder_is_created_and_registered_once(self) -> None:
        default = self.manager.ensure_default(self.home)
        self.manager.ensure_default(self.home)
        self.assertEqual(self.home / "Paddock", default)
        self.assertTrue(default.is_dir())
        self.assertEqual([default], self.manager.list())

    def test_add_canonicalizes_deduplicates_and_remove_forgets_only_the_path(self) -> None:
        projects = self.home / "Code"
        projects.mkdir()
        self.manager.add(projects / ".." / "Code")
        self.manager.add(projects)
        self.assertEqual([projects], self.manager.list())
        self.assertEqual(projects, self.manager.remove(projects))
        self.assertTrue(projects.is_dir())
        self.assertEqual([], self.manager.list())
        with self.assertRaisesRegex(ParkingError, "not parked"):
            self.manager.remove(projects)

    def test_only_immediate_visible_subdirectories_become_sites(self) -> None:
        projects = self.home / "Paddock"
        (projects / "alpha" / "nested").mkdir(parents=True)
        (projects / "beta").mkdir()
        (projects / ".hidden").mkdir()
        (projects / "readme.txt").write_text("not a site", encoding="utf-8")
        self.manager.add(projects)
        discovery = self.manager.discover()
        self.assertEqual(("alpha", "beta"), tuple(site.name for site in discovery.sites))
        self.assertEqual((), discovery.conflicts)

    def test_explicit_link_wins_over_a_parked_site_name(self) -> None:
        projects = self.home / "Paddock"
        parked = projects / "shop"
        explicit = self.home / "special-shop"
        parked.mkdir(parents=True)
        explicit.mkdir()
        self.manager.add(projects)
        self.store.write("sites", {
            "schema_version": 1,
            "sites": {"shop": {
                "name": "shop", "root": str(explicit), "php": "8.4", "secured": False,
            }},
        })
        discovery = self.manager.discover()
        self.assertEqual((), discovery.sites)
        self.assertEqual("explicit link takes precedence", discovery.conflicts[0].reason)

    def test_duplicate_names_across_paths_are_conflicts_and_never_guessed(self) -> None:
        first = self.home / "Client A"
        second = self.home / "Client B"
        (first / "api").mkdir(parents=True)
        (second / "api").mkdir(parents=True)
        self.manager.add(first)
        self.manager.add(second)
        discovery = self.manager.discover()
        self.assertEqual((), discovery.sites)
        self.assertEqual("api", discovery.conflicts[0].name)
        self.assertEqual("duplicate site name across parked paths", discovery.conflicts[0].reason)

    def test_reconcile_materializes_sites_with_the_default_php(self) -> None:
        projects = self.home / "Paddock"
        site = projects / "shop"
        (site / "public").mkdir(parents=True)
        self.manager.add(projects)
        self.store.update(
            "settings", lambda value: {**value, "default_php": "8.4"}
        )
        result = self.manager.reconcile(self.projector, reload=False)
        record = self.store.read("sites")["sites"]["shop"]
        self.assertEqual("parked", record["origin"])
        self.assertEqual(str(projects), record["parking_path"])
        self.assertEqual("8.4", record["php"])
        self.assertEqual(("shop",), tuple(item.name for item in result.sites))
        self.assertIn("server_name shop.test;", promoted(self.projector))

    def test_reconcile_preserves_parked_overrides_and_prunes_removed_folders(self) -> None:
        projects = self.home / "Paddock"
        site = projects / "shop"
        site.mkdir(parents=True)
        self.manager.add(projects)
        self.store.update("settings", lambda value: {**value, "default_php": "8.4"})
        self.manager.reconcile(self.projector, reload=False)
        self.store.update("sites", lambda value: {
            **value,
            "sites": {
                "shop": {**value["sites"]["shop"], "php": "8.5", "secured": True}
            },
        })
        self.manager.reconcile(self.projector, reload=False)
        record = self.store.read("sites")["sites"]["shop"]
        self.assertEqual("8.5", record["php"])
        self.assertTrue(record["secured"])
        site.rmdir()
        self.manager.reconcile(self.projector, reload=False)
        self.assertEqual({}, self.store.read("sites")["sites"])

    def test_reconcile_uses_newest_installed_php_when_no_default_exists(self) -> None:
        projects = self.home / "Paddock"
        (projects / "shop").mkdir(parents=True)
        self.manager.add(projects)
        for version in ("8.4", "8.5"):
            php = self.store.paths.data / f"php-{version}"
            php.write_text("#!/bin/sh\n", encoding="utf-8")
            php.chmod(0o755)
            RuntimeRegistry(self.store).register(version, php, "0" * 64)
        result = self.manager.reconcile(self.projector, reload=False)
        self.assertEqual("8.5", self.store.read("sites")["sites"]["shop"]["php"])
        self.assertEqual((), result.conflicts)

    def test_reconcile_reports_sites_when_no_php_exists(self) -> None:
        projects = self.home / "Paddock"
        (projects / "shop").mkdir(parents=True)
        self.manager.add(projects)
        result = self.manager.reconcile(self.projector, reload=False)
        self.assertEqual({}, self.store.read("sites")["sites"])
        self.assertEqual(
            "no default or installed PHP version is available",
            result.conflicts[0].reason,
        )

    def test_watchers_are_generated_enabled_and_pruned_per_path(self) -> None:
        first = self.home / "Client Projects"
        second = self.home / "Personal"
        first.mkdir()
        second.mkdir()
        self.manager.add(first)
        self.manager.add(second)
        units = self.manager.sync_watchers()
        self.assertEqual(2, len(units))
        self.assertIn(
            "ExecStart=/usr/bin/paddock park --refresh",
            self.manager.reconcile_unit.read_text(encoding="utf-8"),
        )
        rendered = "\n".join(path.read_text(encoding="utf-8") for path in units)
        escaped_first = str(first).replace(" ", "\\x20")
        self.assertIn(f"PathChanged={escaped_first}", rendered)
        self.assertNotIn('PathChanged="', rendered)
        enables = [call for call in self.calls if call[2:4] == ["enable", "--now"]]
        self.assertEqual(2, len(enables))

        stale_name = (
            units[0].name
            if escaped_first in units[0].read_text(encoding="utf-8")
            else units[1].name
        )
        self.manager.remove(first)
        self.manager.sync_watchers()
        self.assertIn(
            ["systemctl", "--user", "disable", "--now", stale_name], self.calls
        )
        self.assertFalse((self.manager.unit_directory / stale_name).exists())


if __name__ == "__main__":
    unittest.main()
