from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from paddock.cli import run
from paddock.execution import plan_composer, plan_php
from paddock.paths import Paths
from paddock.projects import ProjectError, select_php, write_project_selection
from paddock.runtimes import RuntimeError, RuntimeRegistry
from paddock.state import StateStore


class SelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        environment = {
            "HOME": str(base / "home"),
            "XDG_CONFIG_HOME": str(base / "config"),
            "XDG_DATA_HOME": str(base / "data"),
            "XDG_STATE_HOME": str(base / "state"),
            "XDG_CACHE_HOME": str(base / "cache"),
        }
        self.store = StateStore(Paths.from_environment(environment))
        self.store.initialize()
        self.registry = RuntimeRegistry(self.store)
        self.php84 = self._executable(base / "runtime 8.4" / "php", b"php84")
        self.php85 = self._executable(base / "runtime 8.5" / "php", b"php85")
        self.registry.register("8.4", self.php84)
        self.registry.register("8.5", self.php85)
        self.project = base / "Project ü"
        self.nested = self.project / "src" / "nested"
        self.nested.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _executable(path: Path, content: bytes) -> Path:
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        path.chmod(0o755)
        return path

    def _link(self, name: str, root: Path, version: str) -> None:
        def update(value: dict) -> dict:
            sites = dict(value["sites"])
            sites[name] = {
                "name": name,
                "root": str(root.resolve()),
                "php": version,
                "secured": True,
            }
            return {"schema_version": 1, "sites": sites}

        self.store.update("sites", update)

    def test_registry_orders_resolves_and_hashes_runtimes(self) -> None:
        runtimes = self.registry.list()
        self.assertEqual([runtime.version for runtime in runtimes], ["8.4", "8.5"])
        self.assertEqual(
            runtimes[0].sha256, hashlib.sha256(self.php84.read_bytes()).hexdigest()
        )
        self.assertEqual(self.registry.resolve("8.5").path, self.php85.resolve())

    def test_nearest_project_config_beats_link_at_equal_distance(self) -> None:
        self._link("demo", self.project, "8.4")
        write_project_selection(self.project, "8.5")
        selection = select_php(self.nested, self.store)
        self.assertEqual(selection.version, "8.5")
        self.assertIn("project configuration", selection.source)

    def test_nearer_link_beats_more_distant_project_config(self) -> None:
        write_project_selection(self.project, "8.4")
        linked = self.project / "src"
        self._link("nested", linked, "8.5")
        selection = select_php(self.nested, self.store)
        self.assertEqual(selection.version, "8.5")
        self.assertIn("linked site nested", selection.source)

    def test_default_is_used_outside_projects(self) -> None:
        self.store.write("settings", {"schema_version": 1, "default_php": "8.4"})
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        self.assertEqual(select_php(outside, self.store).version, "8.4")

    def test_php_and_composer_plans_use_selected_runtime_directly(self) -> None:
        write_project_selection(self.project, "8.5")
        composer = Path(self.temporary.name) / "composer.phar"
        composer.write_text("composer", encoding="utf-8")
        php = plan_php(self.nested, ["-v"], self.store)
        planned_composer = plan_composer(
            self.nested, ["install"], self.store, composer
        )
        self.assertEqual(php.executable, self.php85.resolve())
        self.assertEqual(php.arguments[-1], "-v")
        self.assertEqual(php.arguments[:2], ("-d", f"extension_dir={self.php85.parent.parent / 'modules'}"))
        self.assertEqual(dict(php.environment)["PHPRC"], str(self.php85.parent.parent / "etc" / "php.ini"))
        self.assertEqual(
            planned_composer.arguments[-2:], (str(composer.resolve()), "install")
        )

    def test_missing_runtime_has_actionable_error(self) -> None:
        write_project_selection(self.project, "8.3")
        with self.assertRaisesRegex(RuntimeError, "php install 8.3"):
            plan_php(self.project, [], self.store)

    def test_malformed_project_config_is_not_ignored(self) -> None:
        (self.project / ".paddock.json").write_text(
            '{"php":"8.4","unknown":true}', encoding="utf-8"
        )
        with self.assertRaises(ProjectError):
            select_php(self.nested, self.store)

    def test_cli_php_list_and_use_are_backed_by_registry(self) -> None:
        environment = {
            "HOME": str(Path(self.temporary.name) / "home"),
            "XDG_CONFIG_HOME": str(self.store.paths.config.parent),
            "XDG_DATA_HOME": str(self.store.paths.data.parent),
            "XDG_STATE_HOME": str(self.store.paths.state.parent),
            "XDG_CACHE_HOME": str(self.store.paths.cache.parent),
        }
        with patch.dict("os.environ", environment, clear=True), patch(
            "pathlib.Path.cwd", return_value=self.project
        ), patch("builtins.print") as output:
            self.assertEqual(run(["php", "list"]), 0)
            self.assertIn("8.4", output.call_args_list[0].args[0])
            self.assertEqual(run(["php", "use", "8.5"]), 0)
        self.assertEqual(select_php(self.project, self.store).version, "8.5")
