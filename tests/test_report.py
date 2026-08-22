"""The machine contract behind `paddock report`.

The Omarchy plugin refreshes on a timer and is instantiated once per monitor,
so this snapshot has to be cheap and total: one call, every fact, and no
exception escaping into a status display. The health rollup drives a coloured
dot in the bar, so a truth table matters more than the field names.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from paddock import report
from paddock.paths import Paths
from paddock.runtimes import RuntimeRegistry
from paddock.services import ServiceManager
from paddock.state import StateStore


class FakeSystemctl:
    """Answers `is-active` per unit, defaulting to active."""

    def __init__(self, states: dict[str, str] | None = None, *, lingering: str = "yes"):
        self.states = states or {}
        self.lingering = lingering
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        if command[0] == "loginctl":
            return subprocess.CompletedProcess(command, 0, self.lingering + "\n", "")
        if "is-active" in command:
            units = command[command.index("is-active") + 1:]
            body = "".join(f"{self.states.get(unit, 'active')}\n" for unit in units)
            return subprocess.CompletedProcess(command, 0, body, "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def queries(self) -> list[list[str]]:
        return [call for call in self.calls if "is-active" in call]


class ReportFixture:
    """A store with one runtime, two sites, and one service."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.paths = Paths(
            config=root / "config" / "paddock", data=root / "data", state=root / "state",
            cache=root / "cache", runtime=root / "run",
        )
        self.store = StateStore(self.paths)
        self.store.initialize()

        release = root / "releases" / "php-8.4.23-0123456789ab" / "bin"
        release.mkdir(parents=True)
        php = release / "php"
        php.write_text("#!/bin/sh\n", encoding="utf-8")
        php.chmod(0o755)
        RuntimeRegistry(self.store).register("8.4", php, "0" * 64)

        self.store.update("sites", lambda current: {**current, "sites": {
            "alpha": {"name": "alpha", "root": str(root / "alpha"),
                      "php": "8.4", "secured": True},
            "plain": {"name": "plain", "root": str(root / "plain"),
                      "php": "8.4", "secured": False},
        }})
        ServiceManager(
            self.store, FakeSystemctl(), which=lambda _: "/usr/bin/podman"
        ).configure("redis")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def build(self, states: dict[str, str] | None = None, *, lingering: str = "yes"):
        self.runner = FakeSystemctl(states, lingering=lingering)
        return report.build(self.store, self.runner)


class PayloadTests(ReportFixture, unittest.TestCase):
    def test_it_is_json_serialisable(self) -> None:
        # The CLI prints this straight to stdout; a stray Path would crash it.
        json.dumps(self.build())

    def test_it_carries_its_own_schema_version(self) -> None:
        # A consumer outside this repo needs to detect an incompatible change.
        self.assertEqual(report.SCHEMA_VERSION, self.build()["schema_version"])

    def test_units_include_one_per_registered_runtime(self) -> None:
        names = [unit["name"] for unit in self.build()["units"]]
        self.assertIn("paddock-php@8.4.service", names)
        # The omission that made `status` report a healthy dead stack.
        for core in report.CORE_UNITS:
            self.assertIn(core, names)

    def test_the_release_is_recovered_from_the_runtime_directory(self) -> None:
        runtime = self.build()["php"]["runtimes"][0]
        self.assertEqual("8.4", runtime["minor"])
        self.assertEqual("8.4.23", runtime["release"])

    def test_an_unrecognised_runtime_layout_yields_no_release(self) -> None:
        # The minor is the identity; the release is decoration and may be absent.
        self.assertIsNone(report._release_for(Path("/opt/php/bin/php")))

    def test_sites_carry_the_scheme_they_are_served_on(self) -> None:
        sites = {site["name"]: site for site in self.build()["sites"]}
        self.assertEqual("https://alpha.test", sites["alpha"]["url"])
        self.assertEqual("http://plain.test", sites["plain"]["url"])
        self.assertTrue(sites["alpha"]["secured"])
        self.assertFalse(sites["plain"]["secured"])

    def test_services_report_their_address_and_image(self) -> None:
        service = self.build()["services"][0]
        self.assertEqual("redis", service["name"])
        self.assertEqual("127.0.0.1:6379", service["address"])
        self.assertEqual("paddock-service-redis.service", service["unit"])


class CostTests(ReportFixture, unittest.TestCase):
    def test_one_batched_query_per_manager(self) -> None:
        # A per-monitor widget on a timer must not fork per unit.
        self.build()
        queries = self.runner.queries()
        self.assertEqual(2, len(queries), queries)
        self.assertTrue(any("--user" in query for query in queries))
        self.assertTrue(any("--user" not in query for query in queries))

    def test_linger_is_not_queried_without_services(self) -> None:
        self.store.update("services", lambda current: {**current, "services": {}})
        self.build()
        self.assertEqual([], [c for c in self.runner.calls if c[0] == "loginctl"])


class HealthTests(ReportFixture, unittest.TestCase):
    """One word drives the bar's colour, so every path is pinned."""

    def test_everything_active_is_ok(self) -> None:
        self.assertEqual("ok", self.build()["health"])

    def test_an_inactive_target_is_down(self) -> None:
        self.assertEqual("down", self.build({"paddock.target": "inactive"})["health"])

    def test_a_dead_php_master_is_degraded(self) -> None:
        # Precisely the case the old three-unit `status` called healthy.
        self.assertEqual(
            "degraded", self.build({"paddock-php@8.4.service": "failed"})["health"]
        )

    def test_a_dead_route_unit_is_degraded(self) -> None:
        self.assertEqual(
            "degraded", self.build({"paddock-dns-route.service": "inactive"})["health"]
        )

    def test_an_inactive_service_is_degraded(self) -> None:
        self.assertEqual(
            "degraded", self.build({"paddock-service-redis.service": "inactive"})["health"]
        )

    def test_services_without_lingering_are_degraded(self) -> None:
        # Up now, gone at logout: lucky, not healthy.
        self.assertEqual("degraded", self.build(lingering="no")["health"])

    def test_lingering_is_irrelevant_without_services(self) -> None:
        self.store.update("services", lambda current: {**current, "services": {}})
        self.assertEqual("ok", self.build(lingering="no")["health"])

    def test_down_wins_over_degraded(self) -> None:
        everything_broken = {
            "paddock.target": "inactive", "paddock-php@8.4.service": "failed",
        }
        self.assertEqual("down", self.build(everything_broken)["health"])

    def test_the_cli_never_reports_unknown(self) -> None:
        # `unknown` means the report could not be produced, which only the
        # caller can observe. The rollup itself must not emit it.
        for states in ({}, {"paddock.target": "inactive"}, {"paddock-caddy.service": "x"}):
            self.assertIn(self.build(states)["health"], {"ok", "degraded", "down"})


class DegradationTests(ReportFixture, unittest.TestCase):
    """A status display that crashes is worse than one saying it does not know."""

    def test_a_missing_systemctl_does_not_raise(self) -> None:
        def missing(command, **kwargs):
            raise FileNotFoundError("systemctl")

        snapshot = report.build(self.store, missing)
        self.assertEqual("down", snapshot["health"])
        self.assertTrue(all(unit["state"] == "unknown" for unit in snapshot["units"]))

    def test_a_short_reply_does_not_shift_answers_onto_other_units(self) -> None:
        # If systemctl is killed mid-write, the remaining units must read
        # unknown rather than inheriting an earlier unit's state.
        def truncated(command, **kwargs):
            if "is-active" in command:
                return subprocess.CompletedProcess(command, 0, "active\n", "")
            return subprocess.CompletedProcess(command, 0, "yes\n", "")

        units = report.build(self.store, truncated)["units"]
        self.assertEqual("active", units[0]["state"])
        self.assertTrue(all(unit["state"] == "unknown" for unit in units[1:]))

    def test_sites_survive_an_unreadable_record(self) -> None:
        self.store.path_for("sites").write_text("{ not json", encoding="utf-8")
        self.assertEqual([], report.build(self.store, FakeSystemctl())["sites"])


if __name__ == "__main__":
    unittest.main()
