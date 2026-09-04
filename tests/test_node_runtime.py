from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from paddock.execution import plan_node
from paddock.node_runtime import NodeInstaller, NodeManifest, NodeRegistry
from paddock.paths import Paths
from paddock.projects import select_node, write_node_selection
from paddock.state import StateStore


class NodeFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.paths = Paths.from_environment({
            "HOME": str(base / "home"), "XDG_CONFIG_HOME": str(base / "config"),
            "XDG_DATA_HOME": str(base / "data"), "XDG_STATE_HOME": str(base / "state"),
            "XDG_CACHE_HOME": str(base / "cache"),
        }, runtime_root=base / "run/paddock")
        self.store = StateStore(self.paths); self.store.initialize()
        self.project = base / "project"; self.project.mkdir()

    def tearDown(self) -> None: self.temporary.cleanup()


class NodeSelectionTests(NodeFixture):
    def test_ecosystem_files_are_understood_and_explicit_selection_wins(self) -> None:
        (self.project / ".nvmrc").write_text("v22.23.2\n", encoding="utf-8")
        self.assertEqual("22", select_node(self.project, self.store).version)
        write_node_selection(self.project, "24")
        self.assertEqual("24", select_node(self.project, self.store).version)

    def test_package_engines_and_default_are_fallbacks(self) -> None:
        (self.project / "package.json").write_text(
            json.dumps({"engines": {"node": ">=22 <25"}}), encoding="utf-8"
        )
        self.assertEqual("22", select_node(self.project, self.store).version)
        (self.project / "package.json").unlink()
        self.store.update("settings", lambda value: {**value, "default_node": "24"})
        self.assertEqual("24", select_node(self.project, self.store).version)

    def test_execution_prepends_the_selected_runtime_for_npm_children(self) -> None:
        root = self.paths.data / "node-24"; (root / "bin").mkdir(parents=True)
        for name in ("node", "npm", "npx"):
            path = root / "bin" / name; path.write_text("#!/bin/sh\n", encoding="utf-8"); path.chmod(0o755)
        NodeRegistry(self.store).register("24", root / "bin/node", "0" * 64)
        write_node_selection(self.project, "24")
        plan = plan_node(self.project, "npm", ["install"], self.store)
        self.assertEqual(root / "bin/npm", plan.executable)
        self.assertTrue(dict(plan.environment)["PATH"].startswith(str(root / "bin")))


class NodeInstallerTests(NodeFixture):
    def test_verified_official_shape_installs_and_activates(self) -> None:
        payload = Path(self.temporary.name) / "payload" / "node-v24.20.0-linux-x64"
        (payload / "bin").mkdir(parents=True)
        node = payload / "bin/node"
        node.write_text("#!/bin/sh\necho v24.20.0\n", encoding="utf-8"); node.chmod(0o755)
        for name in ("npm", "npx"):
            tool = payload / "bin" / name; tool.write_text("#!/bin/sh\n", encoding="utf-8"); tool.chmod(0o755)
        archive = Path(self.temporary.name) / "node.tar.xz"
        with tarfile.open(archive, "w:xz") as output:
            output.add(payload, arcname=payload.name)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest_path = Path(self.temporary.name) / "node-artifacts.json"
        manifest_path.write_text(json.dumps({"schema_version": 1, "artifacts": [{
            "node": "24.20.0", "major": "24", "architecture": "x86_64",
            "url": archive.as_uri(), "sha256": digest,
        }]}), encoding="utf-8")
        destination = NodeInstaller(self.store).install("24", NodeManifest.load(manifest_path))
        self.assertEqual(destination / "bin/node", NodeRegistry(self.store).resolve("24").path)
        self.assertEqual(destination, (self.paths.data / "node-runtimes/active/24").resolve())

