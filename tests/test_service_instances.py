from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from paddock.paths import Paths
from paddock.application import PaddockController
from paddock.service_instances import ServiceInstanceManager
from paddock.services import ServiceError
from paddock.state import StateStore


class Runner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, command, **_kwargs):
        command = list(command)
        self.calls.append(command)
        if "is-active" in command:
            count = len(command) - command.index("is-active") - 1
            return subprocess.CompletedProcess(command, 0, "active\n" * count, "")
        if "is-enabled" in command:
            count = len(command) - command.index("is-enabled") - 1
            return subprocess.CompletedProcess(command, 0, "enabled\n" * count, "")
        if command and command[0] == "journalctl":
            return subprocess.CompletedProcess(command, 0, "first\nsecond\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")


class ServiceInstanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = StateStore(Paths(
            config=root / "config/paddock",
            data=root / "data",
            state=root / "state",
            cache=root / "cache",
            runtime=root / "run",
        ))
        self.store.initialize()
        self.runner = Runner()
        tokens = iter(("a1b2c3d4", "e5f60718", "99999999"))
        self.manager = ServiceInstanceManager(
            self.store,
            self.runner,
            port_available=lambda _host, port: port != 6380,
            token=lambda: next(tokens),
        )
        self.manager.initialize()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_multiple_instances_of_one_type_have_distinct_id_volume_and_port(self) -> None:
        first = self.manager.create("redis", "Application Cache")
        second = self.manager.create("redis", "Queue Cache")
        self.assertEqual("redis-a1b2c3d4", first.id)
        self.assertEqual("redis-e5f60718", second.id)
        self.assertEqual("paddock-redis-a1b2c3d4", first.volume)
        self.assertEqual((6379, 6381), (first.port, second.port))
        self.assertNotEqual(first.unit, second.unit)
        self.assertNotEqual(first.container, second.container)

    def test_display_names_are_unique_case_insensitively(self) -> None:
        self.manager.create("mysql", "Primary Database")
        with self.assertRaisesRegex(ServiceError, "already used"):
            self.manager.create("postgres", "primary database")

    def test_requested_port_must_be_unique_and_available(self) -> None:
        self.manager.create("redis", "Cache", 6379)
        with self.assertRaisesRegex(ServiceError, "port unavailable"):
            self.manager.create("mysql", "Database", 6379)
        with self.assertRaisesRegex(ServiceError, "port unavailable"):
            self.manager.create("mysql", "Database", 6380)

    def test_mailpit_reserves_both_smtp_and_dashboard_ports(self) -> None:
        mailpit = self.manager.create("mailpit", "Mail")
        self.assertEqual(((1025, 1025), (8025, 8025)), self.manager.ports(mailpit.id))
        self.assertEqual("http://127.0.0.1:8025", self.manager.dashboard_url(mailpit.id))
        unit = (self.manager.unit_directory / mailpit.unit).read_text(encoding="utf-8")
        self.assertIn("--publish 127.0.0.1:1025:1025", unit)
        self.assertIn("--publish 127.0.0.1:8025:8025", unit)
        self.assertEqual(
            (
                "MAIL_MAILER=smtp", "MAIL_HOST=127.0.0.1", "MAIL_PORT=1025",
                "MAIL_USERNAME=null", "MAIL_PASSWORD=null", "MAIL_ENCRYPTION=null",
            ),
            self.manager.connection_lines(mailpit.id),
        )

    def test_second_mailpit_moves_both_ports_together(self) -> None:
        first = self.manager.create("mailpit", "Mail One")
        second = self.manager.create("mailpit", "Mail Two")
        self.assertEqual(((1025, 1025), (8025, 8025)), self.manager.ports(first.id))
        self.assertEqual(((1026, 1025), (8026, 8025)), self.manager.ports(second.id))

    def test_an_unchanged_active_port_can_be_saved(self) -> None:
        instance = self.manager.create("redis", "Cache", 6379)
        unavailable = ServiceInstanceManager(
            self.store, self.runner, port_available=lambda _host, _port: False
        )
        updated = unavailable.update(instance.id, "Renamed Cache", 6379)
        self.assertEqual(6379, updated.port)

    def test_an_image_cannot_inject_unit_directives(self) -> None:
        with self.assertRaisesRegex(ServiceError, "invalid image"):
            self.manager.create(
                "redis", "Cache", 6379,
                image="redis:8\nExecStart=/usr/bin/evil",
            )

    def test_renaming_does_not_change_operational_identity(self) -> None:
        before = self.manager.create("postgres", "Main Database", 5432)
        after = self.manager.update(before.id, "Reporting Database", 5433)
        self.assertEqual(before.id, after.id)
        self.assertEqual(before.volume, after.volume)
        self.assertEqual(before.unit, after.unit)
        self.assertEqual("Reporting Database", after.label)
        self.assertEqual(5433, after.port)

    def test_autostart_and_runtime_are_independent(self) -> None:
        instance = self.manager.create("redis", "Cache")
        self.manager.set_autostart(instance.id, True)
        self.manager.control("start", instance.id)
        self.manager.control("stop", instance.id)
        self.assertIn(
            ["systemctl", "--user", "enable", instance.unit], self.runner.calls
        )
        self.assertIn(
            ["systemctl", "--user", "start", instance.unit],
            self.runner.calls,
        )
        self.assertIn(
            ["systemctl", "--user", "stop", instance.unit], self.runner.calls
        )

    def test_remove_deletes_only_the_instances_recorded_volume(self) -> None:
        instance = self.manager.create("mysql", "Disposable Database")
        self.manager.remove(instance.id)
        self.assertEqual([], self.manager.list())
        self.assertIn(
            ["podman", "volume", "rm", "--force", instance.volume], self.runner.calls
        )

    def test_application_snapshot_is_instance_specific_and_batched(self) -> None:
        redis = self.manager.create("redis", "Cache One", 6379)
        mysql = self.manager.create("mysql", "Database One", 3306)
        controller = PaddockController(
            self.store,
            self.runner,
            which=lambda _name: "/usr/bin/podman",
            port_available=lambda _host, port: port != 6380,
        )
        snapshot = controller.service_instances_snapshot()
        by_id = {instance.id: instance for instance in snapshot.instances}
        self.assertEqual({redis.id, mysql.id}, set(by_id))
        self.assertEqual(
            ("REDIS_HOST=127.0.0.1", "REDIS_PORT=6379"),
            by_id[redis.id].connection,
        )
        self.assertTrue(by_id[mysql.id].active)
        active_queries = [call for call in self.runner.calls if "is-active" in call]
        self.assertEqual(1, len(active_queries))

    def test_application_operations_return_fresh_snapshots(self) -> None:
        controller = PaddockController(
            self.store,
            self.runner,
            which=lambda _name: "/usr/bin/podman",
            port_available=lambda _host, _port: True,
        )
        created = controller.create_service_instance(
            "postgres", "Analytics", 5433, True
        )
        self.assertTrue(created.ok)
        instance = created.snapshot.instances[0]
        self.assertIn(
            ["systemctl", "--user", "start", f"paddock-service-{instance.id}.service"],
            self.runner.calls,
        )
        updated = controller.update_service_instance(
            instance.id, "Analytics Database", 5434, False
        )
        self.assertTrue(updated.ok)
        self.assertEqual("Analytics Database", updated.snapshot.instances[0].label)
        logs = controller.service_instance_logs(instance.id, 12)
        self.assertEqual(("first", "second"), logs.lines)
        removed = controller.remove_service_instance(instance.id)
        self.assertTrue(removed.ok)
        self.assertEqual((), removed.snapshot.instances)


if __name__ == "__main__":
    unittest.main()
