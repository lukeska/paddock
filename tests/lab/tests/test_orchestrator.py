from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import tempfile
import unittest

from paddock.projectfile import load as load_project_file


SCRIPT = Path(__file__).parents[1] / "paddock-lab"
SPEC = importlib.util.spec_from_loader("paddock_lab", SourceFileLoader("paddock_lab", str(SCRIPT)))
assert SPEC and SPEC.loader
lab_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab_module)


class FakeLab(lab_module.Lab):
    def __init__(self, park: Path):
        super().__init__(park, executable="paddock")
        self.calls = []
        self.instances = []

    def run(self, *arguments, cwd=None, check=True):
        self.calls.append((arguments, cwd))
        return lab_module.subprocess.CompletedProcess(arguments, 0, "", "")

    def inventory(self):
        return {"schema_version": 1, "instances": self.instances}


class LabOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.park = Path(self.temporary.name) / "Paddock"
        self.park.mkdir()
        self.lab = FakeLab(self.park)
        self.manifest = {
            "schema_version": 1,
            "installation_id": "12345678-1234-1234-1234-123456789abc",
            "park": str(self.park.resolve()),
            "projects": {}, "services": {}, "files": {},
        }
        self.lab.save(self.manifest)

    def tearDown(self):
        self.temporary.cleanup()

    def test_sync_materializes_marked_projects_and_shared_probes(self):
        self.lab.sync(False)
        for name in ("paddock-lab", "paddock-lab-legacy"):
            destination = self.park / name
            self.assertTrue((destination / ".paddock-lab.json").is_file())
            self.assertTrue((destination / "app/PaddockLab/Probes/LabRunner.php").is_file())
            self.assertTrue((destination / "composer.lock").is_file())
            marker = json.loads((destination / ".paddock-lab.json").read_text())
            self.assertEqual(self.manifest["installation_id"], marker["installation_id"])

    def test_sync_preserves_runtime_state(self):
        self.lab.sync(False)
        destination = self.park / "paddock-lab"
        for relative in (".env", "vendor/keep", "node_modules/keep", "storage/logs/keep"):
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("local")
        self.lab.sync(False)
        for relative in (".env", "vendor/keep", "node_modules/keep", "storage/logs/keep"):
            self.assertEqual("local", (destination / relative).read_text())

    def test_local_edit_is_a_conflict_until_force(self):
        self.lab.sync(False)
        target = self.park / "paddock-lab/routes/web.php"
        target.write_text("local edit")
        with self.assertRaisesRegex(lab_module.LabError, "managed file was edited"):
            self.lab.sync(False)
        self.lab.sync(True)
        self.assertNotEqual("local edit", target.read_text())

    def test_unmarked_existing_directory_is_never_overwritten(self):
        (self.park / "paddock-lab").mkdir()
        with self.assertRaisesRegex(lab_module.LabError, "unmarked"):
            self.lab.sync(False)

    def test_foreign_marker_is_rejected(self):
        destination = self.park / "paddock-lab"
        destination.mkdir()
        (destination / ".paddock-lab.json").write_text(json.dumps({"installation_id": "other"}))
        with self.assertRaisesRegex(lab_module.LabError, "another lab"):
            self.lab.sync(False)

    def test_sync_refuses_a_nested_symlink_escape(self):
        self.lab.sync(False)
        destination = self.park / "paddock-lab"
        outside = self.park.parent / "outside"
        outside.mkdir()
        routes = destination / "routes"
        for child in routes.iterdir():
            child.unlink()
        routes.rmdir()
        routes.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(lab_module.LabError, "through a symlink"):
            self.lab.sync(True)
        self.assertEqual([], list(outside.iterdir()))

    def test_remove_refuses_a_service_with_a_foreign_label(self):
        self.lab.sync(False)
        manifest = self.lab.load()
        manifest["services"] = {"redis": "redis-aaaaaaaa"}
        self.lab.save(manifest)
        self.lab.instances = [{"id": "redis-aaaaaaaa", "label": "Personal Redis"}]
        with self.assertRaisesRegex(lab_module.LabError, "not owned"):
            self.lab.remove(True)

    def test_project_removal_is_confined_to_known_children(self):
        outside = self.park.parent / "important"
        outside.mkdir()
        with self.assertRaisesRegex(lab_module.LabError, "outside the lab"):
            self.lab._validate_project_for_removal(outside, self.manifest["installation_id"])

    def test_cli_contract_contains_every_public_command(self):
        commands = lab_module.parser()._subparsers._group_actions[0].choices
        self.assertEqual(
            {"install", "sync", "status", "check", "php-matrix", "node-matrix", "open", "reset", "remove"},
            set(commands),
        )

    def test_fixture_manifests_and_framework_matrix_are_locked(self):
        apps = SCRIPT.parent / "apps"
        expected = {
            "current": {"framework": "v13.", "platform": "8.3.0", "php": "8.5", "node": "24", "reverb": True},
            "legacy": {"framework": "v9.", "platform": "8.0.2", "php": "8.2", "node": "22", "reverb": False},
        }
        for name, wanted in expected.items():
            root = apps / name
            composer = json.loads((root / "composer.json").read_text())
            lock = json.loads((root / "composer.lock").read_text())
            package = json.loads((root / "package.json").read_text())
            package_lock = json.loads((root / "package-lock.json").read_text())
            framework = next(item for item in lock["packages"] if item["name"] == "laravel/framework")
            self.assertTrue(framework["version"].startswith(wanted["framework"]))
            self.assertEqual(wanted["platform"], composer["config"]["platform"]["php"])
            self.assertEqual(package["name"], package_lock["name"])
            self.assertTrue(lock["content-hash"])
            project = load_project_file(root / "paddock.yml")
            self.assertEqual(wanted["php"], project.php)
            self.assertEqual(wanted["node"], project.node)
            self.assertEqual(wanted["reverb"], any(worker.name == "reverb" for worker in project.workers))

    def test_both_apps_register_the_smoke_dashboard_and_reset(self):
        for name in ("current", "legacy"):
            root = SCRIPT.parent / "apps" / name
            routes = (root / "routes/web.php").read_text()
            commands = (root / "routes/console.php").read_text()
            self.assertIn("/paddock/api/smoke", routes)
            self.assertIn("paddock:smoke", commands)
            self.assertIn("paddock:lab-reset", commands)
            self.assertTrue((root / "resources/views/paddock.blade.php").is_file())


if __name__ == "__main__":
    unittest.main()
