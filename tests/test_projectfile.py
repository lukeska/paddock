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

from paddock.web import WebProjector
from paddock.paths import Paths
from paddock.projectfile import (
    DeclaredService, DeclaredWorker, ProjectFile, ProjectFileError, Reconciler, parse,
)
from paddock.runtimes import RuntimeRegistry
from paddock.service_instances import ServiceInstanceManager
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
            "workers": {
                "queue": {"autostart": True},
                "scheduler": None,
                "reverb": {"autostart": False},
            },
            "env": {"APP_ENV": "local", "FEATURE_FLAG": "true"},
        })
        self.assertEqual("my-app", declared.name)
        self.assertEqual("8.5", declared.php)
        self.assertTrue(declared.secure)
        self.assertEqual({"postgres", "redis"}, {s.name for s in declared.services})
        self.assertEqual(
            (
                DeclaredWorker("queue", True),
                DeclaredWorker("scheduler", True),
                DeclaredWorker("reverb", False),
            ),
            declared.workers,
        )
        self.assertEqual(
            (("APP_ENV", "local"), ("FEATURE_FLAG", "true")),
            declared.environment,
        )

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

    def test_workers_are_strict(self) -> None:
        for document in (
            {"workers": {"horizon": None}},
            {"workers": {"queue": {"enabled": True}}},
            {"workers": {"scheduler": {"autostart": "yes"}}},
            {"workers": ["queue"]},
        ):
            with self.subTest(document=document), self.assertRaises(ProjectFileError):
                parse(document)

    def test_environment_keys_and_values_are_strict(self) -> None:
        for document in (
            {"env": {"NOT-VALID": "value"}},
            {"env": {"PORT": 8000}},
            {"env": {"MULTILINE": "first\nsecond"}},
            {"env": {"TAB": "first\tsecond"}},
            {"env": ["APP_ENV=local"]},
        ):
            with self.subTest(document=document), self.assertRaises(ProjectFileError):
                parse(document)

    def test_a_version_only_replaces_the_tag(self) -> None:
        # A project file must not be able to point the machine at any image.
        self.assertEqual(
            "docker.io/library/postgres:16",
            DeclaredService("postgres", version="16").image(),
        )
        self.assertNotIn("..", DeclaredService("postgres", version="16").image())

    def test_no_version_means_the_catalog_default(self) -> None:
        self.assertEqual(
            "docker.io/library/redis:8.10.1", DeclaredService("redis").image()
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
        self.enabled_units: set[str] = set()

        def runner(command, *args, **kwargs):
            self.calls.append(list(command))
            out = "active" if "is-active" in command else ""
            if command[0] == "loginctl":
                out = "yes"
            if "is-active" in command:
                units = command[command.index("is-active") + 1:]
                out = "active\n" * len(units)
            if command[:3] == ["systemctl", "--user", "enable"]:
                self.enabled_units.add(command[-1])
            if command[:3] == ["systemctl", "--user", "disable"]:
                self.enabled_units.discard(command[-1])
            if "is-enabled" in command:
                unit = command[-1]
                enabled = unit in self.enabled_units
                return subprocess.CompletedProcess(
                    command, 0 if enabled else 1,
                    "enabled\n" if enabled else "disabled\n", "",
                )
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
        (self.root / "bootstrap").mkdir()
        (self.root / "bootstrap/app.php").write_text("<?php", encoding="utf-8")
        (self.root / "artisan").write_text("artisan", encoding="utf-8")
        (self.root / "composer.json").write_text(
            '{"require":{"laravel/framework":"^12.0","laravel/reverb":"^1.0"}}',
            encoding="utf-8",
        )

        projector = WebProjector(self.paths, runner)
        self.sites = SiteManager(self.store, projector)
        self.security = SecurityManager(self.store, projector)
        tokens = iter(("11111111", "22222222", "33333333", "44444444"))
        self.services = ServiceInstanceManager(
            self.store, runner, port_available=lambda _host, _port: True,
            token=lambda: next(tokens),
        )
        self.services.initialize()
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

    def test_environment_is_reconciled_without_touching_unrelated_values(self) -> None:
        path = self.root / ".env"
        path.write_text(
            "# keep this comment\nAPP_ENV=production\nLOCAL_ONLY=mine\n",
            encoding="utf-8",
        )
        declared = ProjectFile(
            php="8.5", environment=(("APP_ENV", "local"), ("APP_NAME", "My App")),
        )
        first = self.reconciler.apply(self.root, declared)
        self.assertIn("write 2 environment variables to .env", [step.detail for step in first])
        self.assertEqual(
            '# keep this comment\nAPP_ENV=local\nLOCAL_ONLY=mine\n\nAPP_NAME="My App"\n',
            path.read_text(encoding="utf-8"),
        )
        second = self.reconciler.apply(self.root, declared)
        self.assertIn(".env already matches paddock.yml", [step.detail for step in second])

    def test_environment_dry_run_does_not_create_env(self) -> None:
        steps = self.reconciler.apply(
            self.root,
            ProjectFile(php="8.5", environment=(("APP_ENV", "local"),)),
            dry_run=True,
        )
        self.assertIn("write 1 environment variable to .env", [step.detail for step in steps])
        self.assertFalse((self.root / ".env").exists())


class SharedServiceTests(ReconcilerFixture, unittest.TestCase):
    """A declaration is safe only when its type selects at most one instance."""

    def test_a_declared_service_is_configured_and_started(self) -> None:
        self.reconciler.apply(self.root, ProjectFile(
            php="8.5", services=(DeclaredService("postgres"),)))
        self.assertEqual(["postgres"], [s.type for s in self.services.list()])
        self.assertIn(
            ["systemctl", "--user", "start", "paddock-service-postgres-11111111.service"],
            self.calls,
        )

    def test_typesense_uses_the_catalog_label_image_and_port(self) -> None:
        self.reconciler.apply(self.root, ProjectFile(
            php="8.5", services=(DeclaredService("typesense"),)))
        instance = self.services.list()[0]
        self.assertEqual("Typesense", instance.label)
        self.assertEqual("docker.io/typesense/typesense:30.2", instance.image)
        self.assertEqual(8108, instance.port)

    def test_a_version_disagreement_is_reported_not_imposed(self) -> None:
        self.services.create(
            "postgres", "PostgreSQL", 5432,
            image="docker.io/library/postgres:17",
        )
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
        self.services.create("redis", "Redis", 6379)
        steps = self.reconciler.apply(self.root, ProjectFile(
            php="8.5", services=(DeclaredService("redis", port=6380),)))
        self.assertIn("blocked", self.outcomes(steps))

    def test_a_matching_but_stopped_service_is_started(self) -> None:
        instance = self.services.create("redis", "Redis", 6379)
        stopped = ServiceInstanceManager(
            self.store,
            lambda command, **kw: subprocess.CompletedProcess(command, 0, "inactive\n", ""),
            port_available=lambda _host, _port: True,
        )
        reconciler = Reconciler(self.store, self.sites, self.security, stopped)
        steps = reconciler.apply(self.root, ProjectFile(
            php="8.5", services=(DeclaredService("redis"),)))
        self.assertIn("start Redis", [s.detail for s in steps])

    def test_multiple_instances_are_reported_as_ambiguous(self) -> None:
        first = self.services.create("redis", "Application Cache", 6379)
        second = self.services.create("redis", "Queue Cache", 6380)
        steps = self.reconciler.apply(self.root, ProjectFile(
            php="8.5", services=(DeclaredService("redis"),)))
        blocked = [step for step in steps if step.outcome == "blocked"]
        self.assertEqual(1, len(blocked))
        self.assertIn(first.id, blocked[0].detail)
        self.assertIn(second.id, blocked[0].detail)


class WorkerTests(ReconcilerFixture, unittest.TestCase):
    def test_declared_workers_are_started_and_autostart_is_converged(self) -> None:
        declared = ProjectFile(
            php="8.5",
            workers=(
                DeclaredWorker("queue"),
                DeclaredWorker("scheduler"),
                DeclaredWorker("reverb", autostart=False),
            ),
        )
        first = self.reconciler.apply(self.root, declared)
        details = [step.detail for step in first]
        self.assertIn("start queue for my-app.test", details)
        self.assertIn("start scheduler for my-app.test", details)
        self.assertIn("start reverb for my-app.test", details)
        self.assertIn("enable queue autostart", details)
        self.assertIn("enable scheduler autostart", details)
        self.assertIn("reverb autostart already disabled", details)
        records = self.store.read("sites")["sites"]["my-app"]
        self.assertIn("queue", records)
        self.assertIn("scheduler", records)
        self.assertIn("reverb", records)

        second = self.reconciler.apply(self.root, declared)
        worker_steps = [
            step for step in second
            if any(name in step.detail for name in ("queue", "scheduler", "reverb"))
        ]
        self.assertTrue(worker_steps)
        self.assertEqual({"unchanged"}, {step.outcome for step in worker_steps})

    def test_worker_dry_run_reports_without_writing_units_or_state(self) -> None:
        steps = self.reconciler.apply(
            self.root,
            ProjectFile(php="8.5", workers=(DeclaredWorker("scheduler"),)),
            dry_run=True,
        )
        self.assertIn("start scheduler for my-app.test", [step.detail for step in steps])
        self.assertNotIn(
            "scheduler", self.store.read("sites").get("sites", {}).get("my-app", {})
        )
        self.assertFalse(any("paddock-scheduler" in " ".join(call) for call in self.calls))

    def test_autostart_false_disables_an_enabled_worker_without_stopping_it(self) -> None:
        self.reconciler.apply(
            self.root,
            ProjectFile(php="8.5", workers=(DeclaredWorker("queue"),)),
        )
        unit = "paddock-queue-my-app.service"
        self.assertIn(unit, self.enabled_units)

        steps = self.reconciler.apply(
            self.root,
            ProjectFile(
                php="8.5", workers=(DeclaredWorker("queue", autostart=False),)
            ),
        )
        self.assertIn("disable queue autostart", [step.detail for step in steps])
        self.assertNotIn(unit, self.enabled_units)
        self.assertNotIn(
            ["systemctl", "--user", "stop", unit], self.calls
        )

    def test_unavailable_worker_is_blocked(self) -> None:
        (self.root / "composer.json").write_text(
            '{"require":{"laravel/framework":"^12.0"}}', encoding="utf-8"
        )
        steps = self.reconciler.apply(
            self.root,
            ProjectFile(php="8.5", workers=(DeclaredWorker("reverb"),)),
        )
        blocked = [step for step in steps if step.outcome == "blocked"]
        self.assertEqual(1, len(blocked))
        self.assertIn("laravel/reverb is not installed", blocked[0].detail)

    def test_dry_run_accounts_for_a_declared_reverb_environment(self) -> None:
        (self.root / "composer.json").write_text(
            '{"require":{"laravel/framework":"^12.0"}}', encoding="utf-8"
        )
        steps = self.reconciler.apply(
            self.root,
            ProjectFile(
                php="8.5",
                environment=(("BROADCAST_CONNECTION", "reverb"),),
                workers=(DeclaredWorker("reverb"),),
            ),
            dry_run=True,
        )
        self.assertIn("start reverb for my-app.test", [step.detail for step in steps])
        self.assertFalse(any(step.outcome == "blocked" for step in steps))


if __name__ == "__main__":
    unittest.main()
