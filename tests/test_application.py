"""Contract tests for the application layer used by the native UI."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from paddock.application import (
    PaddockController,
    RedisConfigCandidate,
    validate_redis,
)
from paddock.artifacts import normalized_architecture
from paddock.paths import Paths
from paddock.runtimes import RuntimeRegistry
from paddock.services import CATALOG, Service
from paddock.state import StateStore


class StateRunner:
    def __init__(
        self,
        *,
        active: str = "inactive",
        enabled: str = "disabled",
        linger: str = "yes",
        missing_systemd: bool = False,
    ):
        self.active = active
        self.enabled = enabled
        self.linger = linger
        self.missing_systemd = missing_systemd
        self.calls: list[list[str]] = []

    def __call__(self, command, *args, **kwargs):
        command = list(command)
        self.calls.append(command)
        if command[:2] == ["loginctl", "show-user"]:
            return subprocess.CompletedProcess(command, 0, self.linger + "\n", "")
        if self.missing_systemd and command[:2] == ["systemctl", "--user"]:
            raise FileNotFoundError("systemctl")
        if "is-active" in command:
            return subprocess.CompletedProcess(command, 0, self.active + "\n", "")
        if "is-enabled" in command:
            code = 0 if self.enabled == "enabled" else 1
            return subprocess.CompletedProcess(command, code, self.enabled + "\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")


class MutableRunner(StateRunner):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fail_reload = 0
        self.fail_restart = 0
        self.journal = "one\ntwo\nthree\n"

    def __call__(self, command, *args, **kwargs):
        command = list(command)
        if command == ["systemctl", "--user", "daemon-reload"] and self.fail_reload:
            self.calls.append(command)
            self.fail_reload -= 1
            return subprocess.CompletedProcess(command, 1, "", "reload failed")
        if command[:3] == ["systemctl", "--user", "restart"] and self.fail_restart:
            self.calls.append(command)
            self.fail_restart -= 1
            self.active = "failed"
            return subprocess.CompletedProcess(command, 1, "", "start job timed out")
        if command[:2] == ["journalctl", "--user-unit"]:
            self.calls.append(command)
            return subprocess.CompletedProcess(command, 0, self.journal, "")
        result = super().__call__(command, *args, **kwargs)
        if command[:3] == ["systemctl", "--user", "enable"]:
            self.active = "active"
            self.enabled = "enabled"
        elif command[:3] == ["systemctl", "--user", "start"]:
            self.active = "active"
        elif command[:3] == ["systemctl", "--user", "restart"]:
            self.active = "active"
        elif command[:3] == ["systemctl", "--user", "stop"]:
            self.active = "inactive"
        elif command[:3] == ["systemctl", "--user", "disable"]:
            self.active = "inactive"
            self.enabled = "disabled"
        return result


class DashboardRunner:
    def __init__(self, states: dict[str, str]):
        self.states = dict(states)
        self.enabled = {
            unit for unit in states if unit.startswith("paddock-service-")
        }
        self.calls: list[list[str]] = []

    def __call__(self, command, *args, **kwargs):
        command = list(command)
        self.calls.append(command)
        if command[:2] == ["loginctl", "show-user"]:
            return subprocess.CompletedProcess(command, 0, "yes\n", "")
        if "is-active" in command:
            units = command[command.index("is-active") + 1:]
            output = "".join(f"{self.states.get(unit, 'inactive')}\n" for unit in units)
            return subprocess.CompletedProcess(command, 0, output, "")
        if "is-enabled" in command:
            units = command[command.index("is-enabled") + 1:]
            output = "".join(
                f"{'enabled' if unit in self.enabled else 'disabled'}\n"
                for unit in units
            )
            return subprocess.CompletedProcess(command, 0, output, "")
        if command[:3] == ["systemctl", "--user", "enable"]:
            self.enabled.add(command[-1])
            if "--now" in command:
                self.states[command[-1]] = "active"
        elif command[:3] == ["systemctl", "--user", "disable"]:
            self.enabled.discard(command[-1])
        elif command[:3] == ["systemctl", "--user", "start"]:
            self.states[command[-1]] = "active"
        elif command[:3] == ["systemctl", "--user", "stop"]:
            self.states[command[-1]] = "inactive"
        elif command == ["systemctl", "start", "paddock.target"]:
            for unit in list(self.states):
                if not unit.startswith("paddock-service-"):
                    self.states[unit] = "active"
        elif command == ["systemctl", "stop", "paddock.target"]:
            for unit in list(self.states):
                if not unit.startswith("paddock-service-"):
                    self.states[unit] = "inactive"
        return subprocess.CompletedProcess(command, 0, "", "")


class ApplicationFixture:
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = StateStore(Paths(
            config=root / "config" / "paddock",
            data=root / "data",
            state=root / "state",
            cache=root / "cache",
            runtime=root / "run",
        ))
        self.store.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def configure_redis(
        self, *, image: str = "docker.io/library/redis:8.10.1", port: int = 6379
    ) -> None:
        service = Service("redis", image, port, "paddock-redis")
        self.store.update(
            "services",
            lambda current: {
                **current,
                "services": {**current["services"], "redis": service.as_record()},
            },
        )

    def controller(
        self, runner: StateRunner, *, port_available=lambda _host, _port: True
    ) -> PaddockController:
        return PaddockController(
            self.store,
            runner,
            which=lambda _: "/usr/bin/podman",
            port_available=port_available,
        )


class RedisSnapshotTests(ApplicationFixture, unittest.TestCase):
    def test_unconfigured_snapshot_has_defaults_but_no_claimed_runtime(self) -> None:
        snapshot = self.controller(StateRunner()).redis_snapshot()
        self.assertFalse(snapshot.configured)
        self.assertIsNone(snapshot.image)
        self.assertIsNone(snapshot.port)
        self.assertIsNone(snapshot.address)
        self.assertEqual(CATALOG["redis"].container_port, snapshot.container_port)
        self.assertEqual("not-configured", snapshot.active_state)
        self.assertEqual("not-configured", snapshot.enabled_state)
        self.assertEqual((), snapshot.connection)

    def test_inactive_snapshot_preserves_systemd_and_configuration_state(self) -> None:
        self.configure_redis(port=6380)
        snapshot = self.controller(
            StateRunner(active="inactive", enabled="enabled")
        ).redis_snapshot()
        self.assertTrue(snapshot.configured)
        self.assertEqual("inactive", snapshot.active_state)
        self.assertEqual("enabled", snapshot.enabled_state)
        self.assertEqual("127.0.0.1:6380", snapshot.address)
        self.assertEqual(("REDIS_HOST=127.0.0.1", "REDIS_PORT=6380"), snapshot.connection)

    def test_active_snapshot_is_not_reduced_to_a_boolean(self) -> None:
        self.configure_redis()
        snapshot = self.controller(StateRunner(active="active")).redis_snapshot()
        self.assertEqual("active", snapshot.active_state)

    def test_failed_snapshot_preserves_the_failed_state(self) -> None:
        self.configure_redis()
        snapshot = self.controller(StateRunner(active="failed")).redis_snapshot()
        self.assertEqual("failed", snapshot.active_state)

    def test_missing_systemd_becomes_unknown(self) -> None:
        self.configure_redis()
        snapshot = self.controller(StateRunner(missing_systemd=True)).redis_snapshot()
        self.assertEqual("unknown", snapshot.active_state)
        self.assertEqual("unknown", snapshot.enabled_state)

    def test_lingering_is_part_of_the_snapshot(self) -> None:
        self.configure_redis()
        snapshot = self.controller(StateRunner(linger="no")).redis_snapshot()
        self.assertFalse(snapshot.lingering)


class LinkedSitesSnapshotTests(ApplicationFixture, unittest.TestCase):
    def test_it_lists_all_sites_sorted_with_urls_and_roots(self) -> None:
        root = self.store.paths.data / "projects"
        self.store.write("sites", {
            "schema_version": 1,
            "sites": {
                "shop": {
                    "name": "shop", "root": str(root / "shop"),
                    "php": "8.5", "secured": True,
                },
                "api": {
                    "name": "api", "root": str(root / "api"),
                    "php": "8.4", "secured": False,
                },
            },
        })
        snapshot = self.controller(StateRunner()).linked_sites_snapshot()
        self.assertEqual(("api", "shop"), tuple(site.name for site in snapshot.sites))
        self.assertEqual("http://api.test", snapshot.sites[0].url)
        self.assertEqual("https://shop.test", snapshot.sites[1].url)
        self.assertEqual(str(root / "shop"), snapshot.sites[1].root)

    def test_it_marks_the_site_whose_nginx_fragment_was_rejected(self) -> None:
        project = self.store.paths.data / "projects" / "shop"
        (project / "public").mkdir(parents=True)
        self.store.write("sites", {"schema_version": 1, "sites": {"shop": {
            "name": "shop", "root": str(project), "php": "8.5", "secured": True,
        }}})
        fragment = self.store.paths.config / "nginx/shop.custom.conf"
        fragment.parent.mkdir(parents=True)
        fragment.write_text("broken on;\n", encoding="utf-8")
        status = self.store.paths.state / "nginx-config-watcher.json"
        status.write_text(json.dumps({"paths": [str(fragment)], "error": "rejected"}))

        site = self.controller(StateRunner()).linked_sites_snapshot().sites[0]
        self.assertTrue(site.nginx_config_error)

    def test_php_change_relinks_the_site_and_refreshes_the_snapshot(self) -> None:
        project = self.store.paths.data / "projects" / "shop"
        (project / "public").mkdir(parents=True)
        for version in ("8.4", "8.5"):
            php = self.store.paths.data / "php" / version / "bin/php"
            php.parent.mkdir(parents=True)
            php.write_text("#!/bin/sh\n", encoding="utf-8")
            php.chmod(0o755)
            RuntimeRegistry(self.store).register(version, php, version.replace(".", "") * 32)
        self.store.write("sites", {
            "schema_version": 1,
            "sites": {
                "shop": {
                    "name": "shop", "root": str(project),
                    "php": "8.4", "secured": True,
                },
            },
        })
        result = self.controller(StateRunner()).set_linked_site_php("shop", "8.5")
        self.assertTrue(result.ok, result.detail)
        self.assertEqual("8.5", result.snapshot.sites[0].php)
        self.assertEqual(("8.4", "8.5"), result.snapshot.php_versions)
        self.assertTrue(result.snapshot.sites[0].secured)

    def test_security_toggle_updates_the_protocol(self) -> None:
        project = self.store.paths.data / "projects" / "shop"
        (project / "public").mkdir(parents=True)
        self.store.write("sites", {
            "schema_version": 1,
            "sites": {
                "shop": {
                    "name": "shop", "root": str(project),
                    "php": "8.4", "secured": True,
                },
            },
        })
        result = self.controller(StateRunner()).set_linked_site_secured("shop", False)
        self.assertTrue(result.ok, result.detail)
        self.assertFalse(result.snapshot.sites[0].secured)
        self.assertEqual("http://shop.test", result.snapshot.sites[0].url)

    def test_snapshot_reconciles_new_children_of_parked_folders(self) -> None:
        projects = self.store.paths.data / "Paddock"
        (projects / "new-app" / "public").mkdir(parents=True)
        controller = self.controller(StateRunner())
        controller.parking.add(projects)
        self.store.update("settings", lambda value: {**value, "default_php": "8.4"})
        snapshot = controller.linked_sites_snapshot()
        self.assertEqual(("new-app",), tuple(site.name for site in snapshot.sites))
        record = self.store.read("sites")["sites"]["new-app"]
        self.assertEqual("parked", record["origin"])

    def test_parking_folder_operations_do_not_delete_the_folder(self) -> None:
        projects = self.store.paths.data / "Client Projects"
        projects.mkdir()
        controller = self.controller(StateRunner())
        added = controller.add_parking_path(str(projects))
        self.assertTrue(added.ok, added.detail)
        self.assertIn(str(projects), added.snapshot.paths)
        removed = controller.remove_parking_path(str(projects))
        self.assertTrue(removed.ok, removed.detail)
        self.assertTrue(projects.is_dir())
        self.assertNotIn(str(projects), removed.snapshot.paths)


class DashboardTests(ApplicationFixture, unittest.TestCase):
    def install_php(self, version: str = "8.4") -> None:
        path = self.store.paths.data / "releases" / f"php-{version}.23-abcdef" / "bin/php"
        path.parent.mkdir(parents=True)
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)
        RuntimeRegistry(self.store).register(version, path, "0" * 64)

    def states(self, value: str) -> dict[str, str]:
        return {
            "paddock.target": value,
            "paddock-dns.service": value,
            "paddock-dns-route.service": value,
            "paddock-web.service": value,
            "paddock-php@8.4.service": value,
            "paddock-service-redis.service": value,
        }

    def test_snapshot_lists_core_php_and_configured_instances(self) -> None:
        self.install_php()
        runner = DashboardRunner(self.states("active"))
        controller = self.controller(runner)
        instance = controller.instances.create("redis", "Application Cache", 6379)
        runner.states[instance.unit] = "active"
        runner.enabled.add(instance.unit)
        snapshot = controller.dashboard_snapshot()
        by_key = {service.key: service for service in snapshot.services}
        self.assertEqual(
            {"web", "dns", "php-8.4", instance.id},
            set(by_key),
        )
        self.assertTrue(by_key[instance.id].active)
        self.assertEqual("Application Cache", by_key[instance.id].title)
        self.assertEqual("8.10.1 · Port: 6379", by_key[instance.id].detail)
        self.assertEqual(
            ("REDIS_HOST=127.0.0.1", "REDIS_PORT=6379"),
            by_key[instance.id].connection,
        )
        self.assertTrue(snapshot.all_active)

    def test_start_all_controls_the_target_and_only_configured_services(self) -> None:
        self.install_php()
        runner = DashboardRunner(self.states("inactive"))
        controller = self.controller(runner)
        instance = controller.instances.create("redis", "Cache", 6379)
        runner.states[instance.unit] = "inactive"
        result = controller.set_dashboard_active(True)
        self.assertTrue(result.ok)
        self.assertTrue(result.snapshot.all_active)
        self.assertIn(["systemctl", "start", "paddock.target"], runner.calls)
        self.assertIn(
            ["systemctl", "--user", "start", instance.unit],
            runner.calls,
        )
        self.assertFalse(any("mysql" in part for call in runner.calls for part in call))

    def test_stop_all_stops_user_services_before_the_system_target(self) -> None:
        runner = DashboardRunner(self.states("active"))
        controller = self.controller(runner)
        instance = controller.instances.create("redis", "Cache", 6379)
        runner.states[instance.unit] = "active"
        result = controller.set_dashboard_active(False)
        self.assertTrue(result.ok)
        user_stop = runner.calls.index(
            ["systemctl", "--user", "stop", instance.unit]
        )
        target_stop = runner.calls.index(["systemctl", "stop", "paddock.target"])
        self.assertLess(user_stop, target_stop)
        self.assertFalse(result.snapshot.all_active)


class PhpVersionsSnapshotTests(ApplicationFixture, unittest.TestCase):
    def test_lists_published_and_installed_versions_newest_first(self) -> None:
        manifest = Path(self.temporary.name) / "artifacts.json"
        architecture = normalized_architecture()
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "artifacts": [
                {
                    "php": "8.4.23", "minor": "8.4",
                    "architecture": architecture,
                    "url": "https://example.test/php-8.4.tar.gz", "sha256": "1" * 64,
                },
                {
                    "php": "8.5.8", "minor": "8.5",
                    "architecture": architecture,
                    "url": "https://example.test/php-8.5.tar.gz", "sha256": "2" * 64,
                },
            ],
        }), encoding="utf-8")
        php = self.store.paths.data / "php-8.4"
        php.write_text("#!/bin/sh\n", encoding="utf-8")
        php.chmod(0o755)
        RuntimeRegistry(self.store).register("8.4", php, "0" * 64)

        snapshot = PaddockController(
            self.store, StateRunner(), artifact_paths=(manifest,)
        ).php_versions_snapshot()

        self.assertEqual(["8.5.8", "8.4.23"], [item.release for item in snapshot.versions])
        self.assertFalse(snapshot.versions[0].installed)
        self.assertTrue(snapshot.versions[0].available)
        self.assertTrue(snapshot.versions[1].installed)
        self.assertEqual(str(php.resolve()), snapshot.versions[1].path)

    def test_keeps_an_installed_version_absent_from_the_catalog(self) -> None:
        manifest = Path(self.temporary.name) / "missing.json"
        php = self.store.paths.data / "php-8.3"
        php.write_text("#!/bin/sh\n", encoding="utf-8")
        php.chmod(0o755)
        RuntimeRegistry(self.store).register("8.3", php, "0" * 64)

        snapshot = PaddockController(
            self.store, StateRunner(), artifact_paths=(manifest,)
        ).php_versions_snapshot()

        self.assertEqual("8.3", snapshot.versions[0].release)
        self.assertTrue(snapshot.versions[0].installed)
        self.assertFalse(snapshot.versions[0].available)

    def test_install_uses_the_published_catalog_and_returns_a_fresh_snapshot(self) -> None:
        manifest = Path(self.temporary.name) / "artifacts.json"
        architecture = normalized_architecture()
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "artifacts": [{
                "php": "8.5.8", "minor": "8.5", "architecture": architecture,
                "url": "https://example.test/php-8.5.tar.gz", "sha256": "2" * 64,
            }],
        }), encoding="utf-8")
        controller = PaddockController(
            self.store, StateRunner(), artifact_paths=(manifest,)
        )
        destination = self.store.paths.data / "runtimes/releases/php-8.5.8-test"

        def installed(_installer, minor, _manifest):
            php = destination / "bin/php"
            php.parent.mkdir(parents=True)
            php.write_text("#!/bin/sh\n", encoding="utf-8")
            php.chmod(0o755)
            RuntimeRegistry(self.store).register(minor, php, "2" * 64)
            return destination

        with patch("paddock.application.RuntimeInstaller.install", autospec=True) as install:
            install.side_effect = installed
            result = controller.install_php("8.5")

        self.assertTrue(result.ok, result.detail)
        self.assertTrue(result.snapshot.versions[0].installed)
        install.assert_called_once()

    def test_install_without_a_catalog_is_an_actionable_failure(self) -> None:
        controller = PaddockController(
            self.store, StateRunner(), artifact_paths=(Path("/missing/catalog.json"),)
        )
        result = controller.install_php("8.5")
        self.assertFalse(result.ok)
        self.assertIn("no PHP runtime catalog", result.detail)


class RedisValidationTests(unittest.TestCase):
    def errors(self, image: str, port: object) -> dict[str, str]:
        candidate = RedisConfigCandidate(image=image, port=port)  # type: ignore[arg-type]
        return {error.field: error.code for error in validate_redis(candidate)}

    def test_catalog_default_is_valid(self) -> None:
        self.assertEqual({}, self.errors("docker.io/library/redis:8", 6379))

    def test_registry_ports_and_digests_are_valid(self) -> None:
        self.assertEqual({}, self.errors("registry.test:5000/team/redis:8.2", 6380))
        self.assertEqual({}, self.errors("registry.test/redis@sha256:abcdef0123456789", 6380))

    def test_image_must_be_registry_qualified_and_pinned(self) -> None:
        for image in ("", "redis:8", "docker.io/library/redis", "-redis:8"):
            with self.subTest(image=image):
                self.assertEqual("invalid_image", self.errors(image, 6379)["image"])

    def test_image_cannot_contain_whitespace_or_control_characters(self) -> None:
        for image in (" docker.io/library/redis:8", "docker.io/library/redis:8\n"):
            with self.subTest(image=image):
                self.assertEqual("invalid_image", self.errors(image, 6379)["image"])

    def test_port_must_be_an_unprivileged_integer(self) -> None:
        for port in (True, "6379", 0, 80, 65536):
            with self.subTest(port=port):
                self.assertEqual(
                    "invalid_port",
                    self.errors("docker.io/library/redis:8", port)["port"],
                )


class RedisPlanTests(ApplicationFixture, unittest.TestCase):
    def test_unconfigured_plan_describes_configuration_and_connection(self) -> None:
        plan = self.controller(StateRunner()).plan_redis(
            RedisConfigCandidate("docker.io/library/redis:8", 6380)
        )
        self.assertTrue(plan.valid)
        self.assertEqual("configure", plan.runtime_effect)
        self.assertEqual(["image", "port"], [change.field for change in plan.changes])
        self.assertEqual(
            ["REDIS_HOST", "REDIS_PORT"],
            [change.field for change in plan.connection_changes],
        )
        self.assertTrue(plan.preserves_volume)

    def test_unchanged_plan_has_no_effect(self) -> None:
        self.configure_redis()
        plan = self.controller(StateRunner(active="active")).plan_redis(
            RedisConfigCandidate("docker.io/library/redis:8.10.1", 6379)
        )
        self.assertTrue(plan.valid)
        self.assertFalse(plan.changed)
        self.assertEqual("none", plan.runtime_effect)
        self.assertEqual((), plan.connection_changes)

    def test_active_redis_requires_restart_after_a_change(self) -> None:
        self.configure_redis()
        plan = self.controller(StateRunner(active="active")).plan_redis(
            RedisConfigCandidate("docker.io/library/redis:8.2", 6380)
        )
        self.assertEqual("restart", plan.runtime_effect)
        self.assertEqual(["image", "port"], [change.field for change in plan.changes])
        self.assertEqual("6380", plan.connection_changes[0].after)

    def test_inactive_redis_uses_changes_on_the_next_start(self) -> None:
        self.configure_redis()
        plan = self.controller(StateRunner(active="inactive")).plan_redis(
            RedisConfigCandidate("docker.io/library/redis:8", 6380)
        )
        self.assertEqual("next-start", plan.runtime_effect)

    def test_invalid_plan_contains_field_errors_and_no_changes(self) -> None:
        plan = self.controller(StateRunner()).plan_redis(RedisConfigCandidate("redis", 80))
        self.assertFalse(plan.valid)
        self.assertEqual({"image", "port"}, {error.field for error in plan.errors})
        self.assertEqual((), plan.changes)
        self.assertEqual("none", plan.runtime_effect)


class RedisApplyTests(ApplicationFixture, unittest.TestCase):
    candidate = RedisConfigCandidate("docker.io/library/redis:8.2", 6380)

    def test_add_writes_projects_enables_and_starts_redis(self) -> None:
        runner = MutableRunner()
        result = self.controller(runner).apply_redis(self.candidate)
        self.assertTrue(result.ok)
        self.assertEqual("ok", result.code)
        service = next(iter(self.store.read("services")["services"].values()))
        self.assertEqual(6380, service["port"])
        unit = self.controller(runner).services.unit_path("redis").read_text()
        self.assertIn("127.0.0.1:6380:6379", unit)
        self.assertIn(
            ["systemctl", "--user", "enable", "--now", "paddock-service-redis.service"],
            runner.calls,
        )

    def test_add_can_leave_redis_stopped(self) -> None:
        runner = MutableRunner()
        result = self.controller(runner).apply_redis(self.candidate, start_after_add=False)
        self.assertTrue(result.ok)
        self.assertNotIn("enable", [part for call in runner.calls for part in call])

    def test_port_collision_rejects_before_mutation(self) -> None:
        runner = MutableRunner()
        result = self.controller(
            runner, port_available=lambda _host, _port: False
        ).apply_redis(self.candidate)
        self.assertFalse(result.ok)
        self.assertEqual("port_in_use", result.code)
        self.assertEqual({}, self.store.read("services")["services"])
        self.assertFalse(self.controller(runner).services.unit_path("redis").exists())

    def test_noop_does_not_reload_or_restart(self) -> None:
        self.configure_redis()
        runner = MutableRunner(active="active", enabled="enabled")
        result = self.controller(runner).apply_redis(
            RedisConfigCandidate("docker.io/library/redis:8.10.1", 6379)
        )
        self.assertEqual("unchanged", result.code)
        self.assertNotIn("daemon-reload", [part for call in runner.calls for part in call])
        self.assertNotIn("restart", [part for call in runner.calls for part in call])

    def test_active_redis_is_restarted_after_change(self) -> None:
        self.configure_redis()
        runner = MutableRunner(active="active", enabled="enabled")
        result = self.controller(runner).apply_redis(self.candidate)
        self.assertTrue(result.ok)
        self.assertIn(
            ["systemctl", "--user", "restart", "paddock-service-redis.service"],
            runner.calls,
        )

    def test_inactive_redis_remains_inactive_after_change(self) -> None:
        self.configure_redis()
        runner = MutableRunner(active="inactive", enabled="enabled")
        result = self.controller(runner).apply_redis(self.candidate)
        self.assertTrue(result.ok)
        self.assertEqual("inactive", result.snapshot.active_state)
        self.assertNotIn("restart", [part for call in runner.calls for part in call])

    def test_projection_activation_failure_restores_state_and_unit(self) -> None:
        self.configure_redis()
        controller = self.controller(MutableRunner(active="active", enabled="enabled"))
        controller.services.project(controller.services.require("redis"))
        old_unit = controller.services.unit_path("redis").read_bytes()
        runner = MutableRunner(active="active", enabled="enabled")
        runner.fail_restart = 1
        result = self.controller(runner).apply_redis(self.candidate)
        self.assertFalse(result.ok)
        self.assertEqual("apply_failed_rolled_back", result.code)
        self.assertEqual("succeeded", result.rollback)
        self.assertEqual(6379, self.store.read("services")["services"]["redis"]["port"])
        restored = self.controller(runner).services.unit_path("redis").read_bytes()
        self.assertEqual(old_unit, restored)

    def test_reload_and_rollback_failure_are_distinct(self) -> None:
        self.configure_redis()
        runner = MutableRunner(active="inactive")
        runner.fail_reload = 2
        result = self.controller(runner).apply_redis(self.candidate)
        self.assertFalse(result.ok)
        self.assertEqual("apply_failed_rollback_failed", result.code)
        self.assertEqual("failed", result.rollback)

    def test_mutations_are_serialized_across_controllers(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        second_finished = threading.Event()
        results = []

        def blocking_probe(_host, _port):
            entered.set()
            release.wait(2)
            return True

        first = self.controller(MutableRunner(), port_available=blocking_probe)
        second = self.controller(MutableRunner())
        first_thread = threading.Thread(
            target=lambda: results.append(first.apply_redis(self.candidate))
        )

        def run_second():
            results.append(second.apply_redis(self.candidate))
            second_finished.set()

        second_thread = threading.Thread(target=run_second)
        first_thread.start()
        self.assertTrue(entered.wait(1))
        second_thread.start()
        self.assertFalse(second_finished.wait(0.05))
        release.set()
        first_thread.join(2)
        second_thread.join(2)
        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(["ok", "unchanged"], [result.code for result in results])


class RedisLifecycleAndLogsTests(ApplicationFixture, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.configure_redis()

    def test_start_stop_and_restart_return_structured_results(self) -> None:
        runner = MutableRunner(active="inactive", enabled="disabled")
        controller = self.controller(runner)
        self.assertEqual("Started Redis", controller.start_redis().summary)
        self.assertEqual("Restarted Redis", controller.restart_redis().summary)
        stopped = controller.stop_redis()
        self.assertEqual("Stopped Redis", stopped.summary)
        self.assertEqual("inactive", stopped.snapshot.active_state)

    def test_logs_are_bounded_and_return_plain_lines(self) -> None:
        runner = MutableRunner()
        result = self.controller(runner).redis_logs(2)
        self.assertTrue(result.ok)
        self.assertEqual(("one", "two", "three"), result.lines)
        journal = next(call for call in runner.calls if call[0] == "journalctl")
        self.assertEqual(["--lines", "2"], journal[-2:])

    def test_generic_service_logs_target_the_selected_service_unit(self) -> None:
        runner = MutableRunner()
        controller = self.controller(runner)
        controller.services.configure("mysql")
        result = controller.service_logs("mysql", 12)
        self.assertTrue(result.ok)
        journal = next(call for call in runner.calls if call[0] == "journalctl")
        self.assertIn("paddock-service-mysql.service", journal)
        self.assertEqual(["--lines", "12"], journal[-2:])

    def test_generic_logs_explain_an_unconfigured_service(self) -> None:
        runner = MutableRunner()
        result = self.controller(runner).service_logs("postgres", 12)
        self.assertEqual("not_configured", result.code)
        self.assertNotIn("journalctl", [part for call in runner.calls for part in call])

    def test_invalid_log_limit_never_invokes_journalctl(self) -> None:
        runner = MutableRunner()
        result = self.controller(runner).redis_logs(0)
        self.assertEqual("invalid_limit", result.code)
        self.assertNotIn("journalctl", [part for call in runner.calls for part in call])

    def test_remove_keeps_data_by_default(self) -> None:
        runner = MutableRunner(active="active", enabled="enabled")
        result = self.controller(runner).remove_redis()
        self.assertTrue(result.ok)
        self.assertEqual({}, self.store.read("services")["services"])
        self.assertNotIn("volume", [part for call in runner.calls for part in call])

    def test_delete_data_targets_only_the_recorded_volume(self) -> None:
        runner = MutableRunner(active="active", enabled="enabled")
        result = self.controller(runner).remove_redis(delete_data=True)
        self.assertTrue(result.ok)
        self.assertIn(
            ["podman", "volume", "rm", "--force", "paddock-redis"], runner.calls
        )


if __name__ == "__main__":
    unittest.main()
