from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest

from paddock.application import (
    DashboardOperationResult,
    DashboardSnapshot,
    LinkedSiteView,
    LinkedSitesOperationResult,
    LinkedSitesSnapshot,
    LogResult,
    PhpInstallResult,
    PhpVersionView,
    PhpVersionsSnapshot,
    NodeInstallResult,
    NodeVersionView,
    NodeVersionsSnapshot,
    ParkingOperationResult,
    ParkingSnapshot,
    ServiceInstanceOperationResult,
    ServiceInstancesSnapshot,
)
from paddock.tui_bridge import PROTOCOL_VERSION, serve


class FakeController:
    def __init__(self):
        self.site = LinkedSiteView(
            "linguine", "linguine.test", "https://linguine.test", "8.4", True,
            "/srv/linguine", "22", True, True, "active", True, 8080,
            True, True, "inactive", False,
        )
        self.calls: list[tuple[object, ...]] = []

    def dashboard_snapshot(self):
        return DashboardSnapshot(())

    def service_instances_snapshot(self):
        return ServiceInstancesSnapshot(())

    def linked_sites_snapshot(self):
        return LinkedSitesSnapshot((self.site,), ("8.4",), ("22",))

    def php_versions_snapshot(self):
        return PhpVersionsSnapshot((
            PhpVersionView("8.5", "8.5.8", "x86_64", False, True, None),
            PhpVersionView("8.4", "8.4.23", "x86_64", True, True, "/php/8.4"),
        ), "x86_64")

    def install_php(self, minor):
        self.calls.append(("php", "install", minor))
        return PhpInstallResult(True, f"installed PHP {minor}", None, self.php_versions_snapshot())

    def node_versions_snapshot(self):
        return NodeVersionsSnapshot((
            NodeVersionView("24", "24.8.0", "x86_64", False, True, None),
            NodeVersionView("22", "22.19.0", "x86_64", True, True, "/node/22"),
        ), "x86_64")

    def install_node(self, major):
        self.calls.append(("node", "install", major))
        return NodeInstallResult(True, f"installed Node.js {major}", None, self.node_versions_snapshot())

    def parking_snapshot(self):
        return ParkingSnapshot(("/home/demo/Code",), ())

    def add_parking_path(self, path):
        self.calls.append(("parking", "add", path))
        return ParkingOperationResult(True, f"parked {path}", None, self.parking_snapshot())

    def remove_parking_path(self, path):
        self.calls.append(("parking", "remove", path))
        return ParkingOperationResult(True, f"forgot {path}", None, ParkingSnapshot((), ()))

    def _result(self, summary: str):
        return LinkedSitesOperationResult(True, summary, None, self.linked_sites_snapshot())

    def _service_result(self, summary: str):
        return ServiceInstanceOperationResult(
            True, summary, None, self.service_instances_snapshot()
        )

    def create_service_instance(self, kind, label, port, autostart):
        self.calls.append(("service", "create", kind, label, port, autostart))
        return self._service_result("service added")

    def set_service_instance_active(self, instance_id, active):
        self.calls.append(("service", "active", instance_id, active))
        return self._service_result("service changed")

    def update_service_instance(self, instance_id, label, port, autostart):
        self.calls.append(("service", "update", instance_id, label, port, autostart))
        return self._service_result("service saved")

    def remove_service_instance(self, instance_id):
        self.calls.append(("service", "remove", instance_id))
        return self._service_result("service removed")

    def service_instance_logs(self, instance_id, lines):
        self.calls.append(("service", "logs", instance_id, lines))
        return LogResult(True, "ok", ("service log",))

    def set_queue_active(self, site, active):
        self.calls.append(("active", "queue", site, active))
        return self._result("queue changed")

    def set_dashboard_active(self, active):
        self.calls.append(("dashboard", active))
        return DashboardOperationResult(
            True, "dashboard changed", None, self.dashboard_snapshot()
        )

    def set_reverb_active(self, site, active):
        self.calls.append(("active", "reverb", site, active))
        return self._result("reverb changed")

    def set_queue_autostart(self, site, enabled):
        self.calls.append(("autostart", "queue", site, enabled))
        return self._result("queue autostart changed")

    def set_reverb_autostart(self, site, enabled):
        self.calls.append(("autostart", "reverb", site, enabled))
        return self._result("reverb autostart changed")

    def set_linked_site_php(self, site, version):
        self.calls.append(("site", "php", site, version))
        return self._result("site php changed")

    def set_linked_site_node(self, site, version):
        self.calls.append(("site", "node", site, version))
        return self._result("site node changed")

    def set_linked_site_secured(self, site, secured):
        self.calls.append(("site", "secured", site, secured))
        return self._result("site security changed")

    def queue_logs(self, site, lines):
        self.calls.append(("logs", "queue", site, lines))
        return ("one", "two")

    def reverb_logs(self, site, lines):
        self.calls.append(("logs", "reverb", site, lines))
        return ("three",)


def request(identifier: int, method: str, params=None, protocol=PROTOCOL_VERSION):
    return json.dumps({
        "protocol_version": protocol,
        "id": identifier,
        "method": method,
        "params": {} if params is None else params,
    })


class BridgeTests(unittest.TestCase):
    def invoke(self, *requests: str, palette_path: Path | None = None):
        output = io.StringIO()
        controller = FakeController()
        serve(
            io.StringIO("\n".join(requests) + "\n"),
            output,
            controller,
            palette_path,
        )
        return [json.loads(line) for line in output.getvalue().splitlines()], controller

    def test_snapshot_has_all_application_surfaces(self):
        responses, _ = self.invoke(request(7, "snapshot.get"))
        response = responses[0]
        self.assertEqual(7, response["id"])
        self.assertTrue(response["ok"])
        self.assertEqual(PROTOCOL_VERSION, response["result"]["protocol_version"])
        self.assertEqual("linguine", response["result"]["sites"]["sites"][0]["name"])
        self.assertIn("services", response["result"])
        self.assertIn("dashboard", response["result"])
        self.assertEqual("8.5.8", response["result"]["php"]["versions"][0]["release"])
        self.assertEqual("24.8.0", response["result"]["node"]["versions"][0]["release"])
        self.assertEqual(["/home/demo/Code"], response["result"]["parking"]["paths"])
        self.assertIsNone(response["result"]["theme"])

    def test_snapshot_carries_the_validated_omarchy_palette(self):
        with tempfile.TemporaryDirectory() as temporary:
            palette = Path(temporary) / "colors.toml"
            palette.write_text(
                'mode = "dark"\naccent = "#7AA2F7"\n'
                'selection = "#292E42"\nforeground = "#A9B1D6"\n',
                encoding="utf-8",
            )
            responses, _ = self.invoke(
                request(8, "snapshot.get"), palette_path=palette
            )
        theme = responses[0]["result"]["theme"]
        self.assertEqual("dark", theme["mode"])
        self.assertEqual("#7aa2f7", theme["accent"])
        self.assertEqual("#292e42", theme["selection"])

    def test_worker_mutations_and_logs_are_dispatched(self):
        responses, controller = self.invoke(
            request(1, "worker.set_active", {
                "site": "linguine", "worker": "queue", "active": True,
            }),
            request(2, "worker.set_autostart", {
                "site": "linguine", "worker": "reverb", "enabled": False,
            }),
            request(3, "worker.logs", {
                "site": "linguine", "worker": "queue", "lines": 50,
            }),
        )
        self.assertTrue(all(response["ok"] for response in responses))
        self.assertEqual(["one", "two"], responses[2]["result"]["lines"])
        self.assertEqual([
            ("active", "queue", "linguine", True),
            ("autostart", "reverb", "linguine", False),
            ("logs", "queue", "linguine", 50),
        ], controller.calls)

    def test_dashboard_start_stop_is_dispatched(self):
        responses, controller = self.invoke(request(
            1, "dashboard.set_active", {"active": False}
        ))
        self.assertTrue(responses[0]["ok"])
        self.assertEqual("dashboard changed", responses[0]["result"]["summary"])
        self.assertEqual([("dashboard", False)], controller.calls)

    def test_php_install_is_dispatched(self):
        responses, controller = self.invoke(request(1, "php.install", {"minor": "8.5"}))
        self.assertTrue(responses[0]["ok"])
        self.assertEqual("installed PHP 8.5", responses[0]["result"]["summary"])
        self.assertEqual([("php", "install", "8.5")], controller.calls)

    def test_node_install_is_dispatched(self):
        responses, controller = self.invoke(request(1, "node.install", {"major": "24"}))
        self.assertTrue(responses[0]["ok"])
        self.assertEqual("installed Node.js 24", responses[0]["result"]["summary"])
        self.assertEqual([("node", "install", "24")], controller.calls)

    def test_parking_mutations_are_dispatched(self):
        responses, controller = self.invoke(
            request(1, "parking.add", {"path": "/home/demo/Projects"}),
            request(2, "parking.remove", {"path": "/home/demo/Code"}),
        )
        self.assertTrue(all(response["ok"] for response in responses))
        self.assertEqual([
            ("parking", "add", "/home/demo/Projects"),
            ("parking", "remove", "/home/demo/Code"),
        ], controller.calls)

    def test_service_instance_operations_are_dispatched(self):
        responses, controller = self.invoke(
            request(1, "service.create", {
                "type": "mysql", "label": "App database", "port": 3307,
                "autostart": True,
            }),
            request(2, "service.set_active", {"id": "mysql-a1", "active": False}),
            request(3, "service.update", {
                "id": "mysql-a1", "label": "Database", "port": 3308,
                "autostart": False,
            }),
            request(4, "service.logs", {"id": "mysql-a1", "lines": 25}),
            request(5, "service.remove", {"id": "mysql-a1"}),
        )
        self.assertTrue(all(response["ok"] for response in responses))
        self.assertEqual(["service log"], responses[3]["result"]["lines"])
        self.assertEqual([
            ("service", "create", "mysql", "App database", 3307, True),
            ("service", "active", "mysql-a1", False),
            ("service", "update", "mysql-a1", "Database", 3308, False),
            ("service", "logs", "mysql-a1", 25),
            ("service", "remove", "mysql-a1"),
        ], controller.calls)

    def test_site_configuration_mutations_are_dispatched(self):
        responses, controller = self.invoke(
            request(1, "site.set_php", {"site": "linguine", "version": "8.5"}),
            request(2, "site.set_node", {"site": "linguine", "version": "24"}),
            request(3, "site.set_secured", {"site": "linguine", "secured": False}),
        )
        self.assertTrue(all(response["ok"] for response in responses))
        self.assertEqual([
            ("site", "php", "linguine", "8.5"),
            ("site", "node", "linguine", "24"),
            ("site", "secured", "linguine", False),
        ], controller.calls)

    def test_bad_request_does_not_end_the_stream(self):
        responses, _ = self.invoke(
            "not json",
            request(2, "snapshot.get", protocol=99),
            request(3, "missing.method"),
            request(4, "snapshot.get"),
        )
        self.assertEqual([
            "invalid_request", "incompatible_protocol", "unknown_method"
        ], [response["error"]["code"] for response in responses[:3]])
        self.assertEqual(2, responses[1]["id"])
        self.assertTrue(responses[3]["ok"])

    def test_params_are_strictly_validated(self):
        responses, controller = self.invoke(request(1, "worker.set_active", {
            "site": "linguine", "worker": "queue", "active": 1,
        }))
        self.assertEqual("invalid_params", responses[0]["error"]["code"])
        self.assertEqual([], controller.calls)

    def test_unknown_envelope_fields_are_rejected(self):
        payload = json.loads(request(6, "snapshot.get"))
        payload["extra"] = True
        responses, _ = self.invoke(json.dumps(payload))
        self.assertEqual(6, responses[0]["id"])
        self.assertEqual("invalid_request", responses[0]["error"]["code"])


if __name__ == "__main__":
    unittest.main()
