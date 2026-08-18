from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


def load_helper():
    path = Path(__file__).parents[1] / "system/system-helper"
    loader = importlib.machinery.SourceFileLoader("paddock_system_helper", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class PackageLifecycleTests(unittest.TestCase):
    def test_recorded_package_removal_preserves_user_data(self):
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installation = root / "integration.json"
            system_ca = root / "rootCA.pem"
            unit = root / "paddock.target"
            data = root / "user-data"
            state = root / "user-state"
            private_ca = data / "pki/rootCA-key.pem"
            private_ca.parent.mkdir(parents=True)
            private_ca.write_text("private", encoding="utf-8")
            state.mkdir()
            system_ca.write_text("public", encoding="utf-8")
            installation.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "user": "demo",
                        "data_dir": str(data),
                        "state_dir": str(state),
                    }
                ),
                encoding="utf-8",
            )
            unit.write_text("unit", encoding="utf-8")
            calls = []

            def fake_run(command, **kwargs):
                calls.append((command, kwargs))

            with (
                patch.object(helper, "INSTALLATION", installation),
                patch.object(helper, "SYSTEM_CA", system_ca),
                patch.object(helper, "OWNED", (str(installation), str(system_ca), str(unit))),
                patch.object(helper, "run", fake_run),
                patch.object(
                    helper.pwd,
                    "getpwnam",
                    return_value=SimpleNamespace(pw_dir=str(root / "home")),
                ),
            ):
                helper.package_remove()

            self.assertTrue(private_ca.is_file())
            self.assertTrue(state.is_dir())
            self.assertFalse(installation.exists())
            self.assertFalse(system_ca.exists())
            self.assertFalse(unit.exists())
            commands = [call[0] for call in calls]
            self.assertIn(["systemctl", "disable", "--now", "paddock.target"], commands)
            self.assertIn(["nmcli", "connection", "delete", "paddock-dns"], commands)
            self.assertIn(["trust", "anchor", "--remove", str(system_ca)], commands)

    def test_unrecorded_package_removal_still_cleans_reserved_resources(self):
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installation = root / "missing.json"
            unit = root / "paddock.target"
            unit.write_text("unit", encoding="utf-8")
            calls = []
            with (
                patch.object(helper, "INSTALLATION", installation),
                patch.object(helper, "SYSTEM_CA", root / "missing-ca.pem"),
                patch.object(helper, "OWNED", (str(unit),)),
                patch.object(helper, "run", lambda command, **kwargs: calls.append(command)),
            ):
                helper.package_remove()

            self.assertFalse(unit.exists())
            self.assertIn(["systemctl", "disable", "--now", "paddock.target"], calls)
            self.assertIn(["systemctl", "daemon-reload"], calls)


if __name__ == "__main__":
    unittest.main()
