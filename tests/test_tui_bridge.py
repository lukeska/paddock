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

    def _result(self, summary: str):
        return LinkedSitesOperationResult(True, summary, None, self.linked_sites_snapshot())

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

    def test_snapshot_has_all_three_application_surfaces(self):
        responses, _ = self.invoke(request(7, "snapshot.get"))
        response = responses[0]
        self.assertEqual(7, response["id"])
        self.assertTrue(response["ok"])
        self.assertEqual(PROTOCOL_VERSION, response["result"]["protocol_version"])
        self.assertEqual("linguine", response["result"]["sites"]["sites"][0]["name"])
        self.assertIn("services", response["result"])
        self.assertIn("dashboard", response["result"])
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
