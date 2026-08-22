"""`paddock.yml` and the reconciler behind `paddock init`.

Two properties carry the feature. Applying is idempotent, so a second run
changes nothing. And it never imposes this project's wishes on state other
projects share: supporting services are one instance per machine by ADR 0010,
so a disagreement is reported rather than resolved.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from paddock.caddy import CaddyProjector
from paddock.paths import Paths
from paddock.projectfile import (
    DeclaredService, ProjectFile, ProjectFileError, Reconciler, parse,
)
from paddock.runtimes import RuntimeRegistry
from paddock.services import ServiceManager
from paddock.sites import SiteManager
from paddock.state import StateStore
from paddock.tls import SecurityManager


class SchemaTests(unittest.TestCase):
    """Strict on purpose: a typo in a committed file must fail on the first
    machine, not do nothing quietly on all of them."""

    def test_an_empty_file_is_valid(self) -> None:
        # "Link this directory, decide nothing else" is a reasonable ask.
        self.assertEqual(ProjectFile(), parse(None))

    def test_a_full_document_parses(self) -> None:
        declared = parse({
            "name": "my-app", "php": "8.5", "secure": True,
            "services": {"postgres": {"version": "16"}, "redis": None},
        })
        self.assertEqual("my-app", declared.name)
        self.assertEqual("8.5", declared.php)
        self.assertTrue(declared.secure)
        self.assertEqual({"postgres", "redis"}, {s.name for s in declared.services})

    def test_an_unknown_key_is_refused(self) -> None:
        with self.assertRaises(ProjectFileError) as caught:
            parse({"naem": "typo"})
        self.assertIn("naem", str(caught.exception))

    def test_a_planned_key_says_so_rather_than_unknown(self) -> None:
        # "aliases" is in the roadmap; calling it a typo would mislead.
        with self.assertRaises(ProjectFileError) as caught:
            parse({"aliases": ["api.my-app"]})
        self.assertIn("not supported yet", str(caught.exception))

    def test_an_unquoted_php_version_is_refused(self) -> None:
        # YAML decodes a bare 8.5 to a float, and 8.10 would lose its zero.
        with self.assertRaises(ProjectFileError) as caught:
            parse({"php": 8.5})
        self.assertIn("quoted", str(caught.exception))

    def test_an_unknown_service_is_refused_and_lists_the_real_ones(self) -> None:
        with self.assertRaises(ProjectFileError) as caught:
            parse({"services": {"mongo": None}})
        self.assertIn("postgres", str(caught.exception))

    def test_secure_must_be_a_boolean(self) -> None:
        with self.assertRaises(ProjectFileError):
            parse({"secure": "yes"})

    def test_a_version_only_replaces_the_tag(self) -> None:
        # A project file must not be able to point the machine at any image.
        self.assertEqual(
            "docker.io/library/postgres:16",
            DeclaredService("postgres", version="16").image(),
        )
        self.assertNotIn("..", DeclaredService("postgres", version="16").image())

    def test_no_version_means_the_catalog_default(self) -> None:
        self.assertEqual(
            "docker.io/library/redis:8", DeclaredService("redis").image()
        )


class ReconcilerFixture:
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.paths = Paths(
            config=base / "config" / "paddock", data=base / "data", state=base / "state",
            cache=base / "cache", runtime=base / "run",
        )
        self.store = StateStore(self.paths)
        self.store.initialize()
        self.calls: list[list[str]] = []

        def runner(command, *args, **kwargs):
            self.calls.append(list(command))
            out = "active" if "is-active" in command else ""
            if command[0] == "loginctl":
                out = "yes"
            if "is-active" in command:
                units = command[command.index("is-active") + 1:]
                out = "active\n" * len(units)
            return subprocess.CompletedProcess(command, 0, out, "")

        self.runner = runner
        release = base / "php-8.5.8-0123456789ab" / "bin"
        release.mkdir(parents=True)
        php = release / "php"
        php.write_text("#!/bin/sh\n", encoding="utf-8")
        php.chmod(0o755)
        RuntimeRegistry(self.store).register("8.5", php, "0" * 64)

        self.root = base / "my-app"
        (self.root / "public").mkdir(parents=True)

        projector = CaddyProjector(self.paths, runner)
        self.sites = SiteManager(self.store, projector)
        self.security = SecurityManager(self.store, projector)
        self.services = ServiceManager(
            self.store, runner, which=lambda _: "/usr/bin/podman"
        )
        self.reconciler = Reconciler(self.store, self.sites, self.security, self.services)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def outcomes(self, steps) -> list[str]:
        return [step.outcome for step in steps]


class ReconcilerTests(ReconcilerFixture, unittest.TestCase):
    def test_a_fresh_project_is_linked(self) -> None:
        steps = self.reconciler.apply(self.root, ProjectFile(php="8.5"))
        self.assertIn("changed", self.outcomes(steps))
        self.assertEqual(["my-app"], [site.name for site in self.sites.list()])

    def test_applying_twice_changes_nothing_the_second_time(self) -> None:
        declared = ProjectFile(php="8.5", services=(DeclaredService("redis"),))
        self.reconciler.apply(self.root, declared)
        again = self.reconciler.apply(self.root, declared)
        self.assertEqual({"unchanged"}, set(self.outcomes(again)), [s.detail for s in again])

    def test_the_site_name_defaults_to_the_directory(self) -> None:
        self.reconciler.apply(self.root, ProjectFile(php="8.5"))
        self.assertEqual("my-app", self.sites.list()[0].name)

    def test_a_declared_name_wins(self) -> None:
        self.reconciler.apply(self.root, ProjectFile(name="shop", php="8.5"))
        self.assertEqual("shop", self.sites.list()[0].name)

    def test_a_name_owned_by_another_directory_is_blocked(self) -> None:
        other = self.root.parent / "other"
        (other / "public").mkdir(parents=True)
        self.sites.link(other, "my-app", "8.5")
        steps = self.reconciler.apply(self.root, ProjectFile(php="8.5"))
        self.assertEqual(["blocked"], self.outcomes(steps))
        self.assertEqual(other, self.sites.list()[0].root)

    def test_a_php_change_is_applied_to_an_existing_link(self) -> None:
        self.sites.link(self.root, "my-app", "8.5")
        release = self.paths.data / "php-8.4.1-abc" / "bin"
        release.mkdir(parents=True)
        php = release / "php"
        php.write_text("#!/bin/sh\n", encoding="utf-8")
        php.chmod(0o755)
        RuntimeRegistry(self.store).register("8.4", php, "1" * 64)
        steps = self.reconciler.apply(self.root, ProjectFile(php="8.4"))
        self.assertIn("changed", self.outcomes(steps))
        self.assertEqual("8.4", self.sites.list()[0].php)

    def test_dry_run_reports_without_acting(self) -> None:
        steps = self.reconciler.apply(self.root, ProjectFile(php="8.5"), dry_run=True)
        self.assertIn("changed", self.outcomes(steps))
        self.assertEqual([], self.sites.list())


class SharedServiceTests(ReconcilerFixture, unittest.TestCase):
    """One instance per machine, so this project does not get to repoint it."""

    def test_a_declared_service_is_configured_and_started(self) -> None:
        self.reconciler.apply(self.root, ProjectFile(
            php="8.5", services=(DeclaredService("postgres"),)))
        self.assertEqual(["postgres"], [s.name for s in self.services.list()])
        self.assertIn(
            ["systemctl", "--user", "enable", "--now", "paddock-service-postgres.service"],
            self.calls,
        )

    def test_a_version_disagreement_is_reported_not_imposed(self) -> None:
        self.services.configure("postgres", "docker.io/library/postgres:17")
        steps = self.reconciler.apply(self.root, ProjectFile(
            php="8.5", services=(DeclaredService("postgres", version="16"),)))
        blocked = [s for s in steps if s.outcome == "blocked"]
        self.assertEqual(1, len(blocked))
        self.assertIn("postgres:17", blocked[0].detail)
        self.assertIn("postgres:16", blocked[0].detail)
        # Untouched: another project may be using it.
        self.assertEqual(
            "docker.io/library/postgres:17", self.services.list()[0].image
        )

    def test_a_port_disagreement_is_also_reported(self) -> None:
        self.services.configure("redis")
        steps = self.reconciler.apply(self.root, ProjectFile(
            php="8.5", services=(DeclaredService("redis", port=6380),)))
        self.assertIn("blocked", self.outcomes(steps))

    def test_a_matching_but_stopped_service_is_started(self) -> None:
        self.services.configure("redis")
        stopped = ServiceManager(
            self.store,
            lambda command, **kw: subprocess.CompletedProcess(command, 0, "inactive\n", ""),
            which=lambda _: "/usr/bin/podman",
        )
        reconciler = Reconciler(self.store, self.sites, self.security, stopped)
        steps = reconciler.apply(self.root, ProjectFile(
            php="8.5", services=(DeclaredService("redis"),)))
        self.assertIn("start redis", [s.detail for s in steps])


if __name__ == "__main__":
    unittest.main()
