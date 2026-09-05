from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


def load_helper():
    # Compile from source text rather than SourceFileLoader: that loader
    # caches bytecode under system/__pycache__, and a stale entry would make
    # these tests read a previous revision of the helper. The version/digest
    # guard exists to catch template edits, so it must never see cached source.
    path = Path(__file__).parents[1] / "system/system-helper"
    module = ModuleType("paddock_system_helper")
    module.__file__ = str(path)
    code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
    exec(code, module.__dict__)
    return module


class PackageLifecycleTests(unittest.TestCase):
    def test_dns_route_explicitly_integrates_with_systemd_resolved(self):
        source = (Path(__file__).parents[1] / "system/system-helper").read_text(
            encoding="utf-8"
        )
        self.assertIn("ExecStart=/usr/bin/resolvectl dns paddock0 127.0.0.1", source)
        self.assertIn("ExecStart=/usr/bin/resolvectl domain paddock0 ~test", source)
        self.assertIn("ExecStop=/usr/bin/resolvectl revert paddock0", source)

    def test_only_the_recorded_owner_can_replace_an_installation(self):
        helper = load_helper()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installation = root / "integration.json"
            data = root / "data"
            state = root / "state"
            installation.write_text(json.dumps({
                "version": 1,
                "user": "demo",
                "data_dir": str(data),
                "state_dir": str(state),
                "linger_enabled_by_paddock": True,
            }), encoding="utf-8")
            with patch.object(helper, "INSTALLATION", installation):
                self.assertTrue(helper.owned_installation("demo", data, state))
                with self.assertRaisesRegex(SystemExit, "another user or location"):
                    helper.owned_installation("other", data, state)

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
            # A machine upgraded from a release that served with Caddy still
            # has that unit; removal must clean it even though it is no longer
            # something this release installs.
            legacy_unit = root / "paddock-caddy.service"
            legacy_unit.write_text("unit", encoding="utf-8")
            calls = []

            def fake_run(command, **kwargs):
                calls.append((command, kwargs))

            with (
                patch.object(helper, "INSTALLATION", installation),
                patch.object(helper, "SYSTEM_CA", system_ca),
                patch.object(helper, "OWNED", (str(installation), str(system_ca), str(unit))),
                patch.object(helper, "LEGACY_UNITS", (str(legacy_unit),)),
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
            self.assertFalse(legacy_unit.exists())
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
            legacy_unit = root / "paddock-caddy.service"
            legacy_unit.write_text("unit", encoding="utf-8")
            calls = []
            with (
                patch.object(helper, "INSTALLATION", installation),
                patch.object(helper, "SYSTEM_CA", root / "missing-ca.pem"),
                patch.object(helper, "OWNED", (str(unit),)),
                patch.object(helper, "LEGACY_UNITS", (str(legacy_unit),)),
                patch.object(helper, "run", lambda command, **kwargs: calls.append(command)),
            ):
                helper.package_remove()

            self.assertFalse(unit.exists())
            self.assertFalse(legacy_unit.exists())
            self.assertIn(["systemctl", "disable", "--now", "paddock.target"], calls)
            self.assertIn(["systemctl", "daemon-reload"], calls)


if __name__ == "__main__":
    unittest.main()
