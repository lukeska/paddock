"""Supporting services: state, unit rendering, and lifecycle targeting.

Services run as rootless containers in the *user* systemd manager, so unlike
every other Paddock unit there is no privilege boundary to defend here: the
unit belongs to the same account that writes it. An earlier draft used a
root-owned unit plus a packaged helper that rebuilt every podman argument from
a validated config file; rootless removed the reason for that helper and these
tests replaced its attack coverage with the properties that still matter.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from paddock.paths import Paths
from paddock.schemas import SchemaError
from paddock.services import CATALOG, ENGINE, ServiceError, ServiceManager
from paddock.state import StateStore


def recording_runner(calls: list[list[str]], code: int = 0, out: str = "active"):
    def runner(command, *args, **kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, code, out, "")
    return runner


class ServiceFixture:
    """Shared sandbox. Not a TestCase, so subclasses re-run nothing."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.paths = Paths(
            config=root / "config" / "paddock", data=root / "data", state=root / "state",
            cache=root / "cache", runtime=root / "run",
        )
        self.store = StateStore(self.paths)
        self.store.initialize()
        self.calls: list[list[str]] = []
        self.manager = ServiceManager(
            self.store, recording_runner(self.calls), which=lambda _: f"/usr/bin/{ENGINE}"
        )
        self.without_engine = ServiceManager(
            self.store, recording_runner(self.calls), which=lambda _: None
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()


class ServiceManagerTests(ServiceFixture, unittest.TestCase):
    def test_configure_uses_the_catalog_defaults(self) -> None:
        service = self.manager.configure("redis")
        self.assertEqual(CATALOG["redis"].image, service.image)
        self.assertEqual("127.0.0.1:6379", service.address)

    def test_the_catalog_image_is_registry_qualified_and_pinned(self) -> None:
        # A bare `redis:8` resolves through the caller's registry search list.
        for name, entry in CATALOG.items():
            self.assertTrue(entry.image.startswith("docker.io/"), name)
            self.assertIn(":", entry.image.rsplit("/", 1)[-1], name)

    def test_every_catalog_port_is_unprivileged(self) -> None:
        # Rootless podman cannot bind below 1024.
        for name, entry in CATALOG.items():
            self.assertGreater(entry.port, 1023, name)
            self.assertGreater(entry.container_port, 1023, name)

    def test_configure_persists_and_writes_a_unit(self) -> None:
        service = self.manager.configure("redis")
        self.assertEqual(["redis"], [s.name for s in self.manager.list()])
        self.assertTrue(self.manager.unit_path("redis").is_file())
        self.assertEqual("paddock-service-redis.service", service.unit)

    def test_the_unit_lives_in_the_user_manager_directory(self) -> None:
        # Never /etc/systemd/system: writing a service must need no privilege.
        path = self.manager.unit_path("redis")
        self.assertEqual(("systemd", "user"), path.parent.parts[-2:])
        self.assertNotIn("/etc/", str(path))

    def test_overrides_are_remembered(self) -> None:
        self.manager.configure("redis", port=6380)
        self.assertEqual(6380, self.manager.require("redis").port)
        # A later call without the override must not silently reset it.
        self.assertEqual(6380, self.manager.configure("redis").port)
        self.assertIn("127.0.0.1:6380:6379", self.manager.unit_path("redis").read_text())

    def test_an_out_of_range_port_is_refused_by_the_schema(self) -> None:
        with self.assertRaises((SchemaError, ValueError)):
            self.manager.configure("redis", port=70000)

    def test_an_unknown_service_is_refused(self) -> None:
        with self.assertRaises(ServiceError) as caught:
            self.manager.configure("mongo")
        self.assertIn("redis", str(caught.exception))

    def test_start_enables_so_the_service_returns_after_a_reboot(self) -> None:
        self.manager.configure("redis")
        self.manager.control("start", "redis")
        self.assertIn(
            ["systemctl", "--user", "enable", "--now", "paddock-service-redis.service"],
            self.calls,
        )

    def test_stop_does_not_disable(self) -> None:
        # Stopping for now must not mean stopping forever.
        self.manager.configure("redis")
        self.manager.control("stop", "redis")
        self.assertIn(
            ["systemctl", "--user", "stop", "paddock-service-redis.service"], self.calls
        )
        self.assertNotIn("disable", [part for call in self.calls for part in call])

    def test_every_lifecycle_call_targets_the_user_manager(self) -> None:
        self.manager.configure("redis")
        self.manager.control("start", "redis")
        self.manager.control("restart", "redis")
        self.manager.state_of(self.manager.require("redis"))
        for call in self.calls:
            if call[:1] == ["systemctl"]:
                self.assertEqual("--user", call[1], call)

    def test_control_reports_a_failure_instead_of_pretending(self) -> None:
        self.manager.configure("redis")
        manager = ServiceManager(
            self.store, recording_runner([], code=1, out="boom"),
            which=lambda _: f"/usr/bin/{ENGINE}",
        )
        with self.assertRaises(ServiceError):
            manager.control("start", "redis")

    def test_controlling_an_unconfigured_service_explains_the_next_step(self) -> None:
        with self.assertRaises(ServiceError) as caught:
            self.manager.control("start", "redis")
        self.assertIn("service add redis", str(caught.exception))

    def test_remove_keeps_the_volume_by_default(self) -> None:
        self.manager.configure("redis")
        self.manager.remove("redis", delete_data=False)
        self.assertEqual([], self.manager.list())
        self.assertFalse(self.manager.unit_path("redis").exists())
        self.assertNotIn("volume", [part for call in self.calls for part in call])

    def test_remove_deletes_the_volume_only_when_asked(self) -> None:
        self.manager.configure("redis")
        self.manager.remove("redis", delete_data=True)
        self.assertIn([ENGINE, "volume", "rm", "--force", "paddock-redis"], self.calls)

    def test_remove_disables_so_it_does_not_come_back_at_boot(self) -> None:
        self.manager.configure("redis")
        self.manager.remove("redis", delete_data=False)
        self.assertIn(
            ["systemctl", "--user", "disable", "--now", "paddock-service-redis.service"],
            self.calls,
        )

    def test_reproject_rewrites_every_configured_service(self) -> None:
        self.manager.configure("redis")
        self.manager.unit_path("redis").unlink()
        self.assertEqual(["redis"], self.manager.reproject())
        self.assertTrue(self.manager.unit_path("redis").is_file())

    def test_a_stale_unit_is_replaced_on_start(self) -> None:
        # An upgrade that changes what the unit should say must take effect
        # without the user knowing to re-add the service.
        self.manager.configure("redis")
        self.manager.unit_path("redis").write_text("[Service]\nExecStart=/bin/false\n")
        self.manager.control("start", "redis")
        self.assertIn(ENGINE, self.manager.unit_path("redis").read_text())

    def test_writing_a_unit_leaves_the_shared_directory_alone(self) -> None:
        # ~/.config/systemd/user holds the user's own units. Paddock's state
        # writer clamps its own directories to 0700; doing that here would be
        # an unrequested change to someone else's files.
        directory = self.manager.unit_directory
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o755)
        self.manager.configure("redis")
        self.assertEqual(0o755, directory.stat().st_mode & 0o777)
        self.assertEqual(0o644, self.manager.unit_path("redis").stat().st_mode & 0o777)

    def test_daemon_reload_follows_a_unit_write(self) -> None:
        # systemd would otherwise keep serving the previous generation.
        self.manager.configure("redis")
        self.assertIn(["systemctl", "--user", "daemon-reload"], self.calls)


class UnitRenderingTests(ServiceFixture, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.unit = self.manager.render(self.manager.configure("redis"))

    def test_the_published_port_is_loopback_only(self) -> None:
        # A development service must never be reachable from the network.
        self.assertIn("--publish 127.0.0.1:6379:6379", self.unit)
        self.assertNotIn("--publish 0.0.0.0", self.unit)

    def test_readiness_is_gated(self) -> None:
        # Without this the unit reports started as soon as podman forks.
        self.assertIn("Type=notify", self.unit)
        self.assertIn("--sdnotify=conmon", self.unit)

    def test_the_image_is_separated_from_the_flags(self) -> None:
        # An image reference shaped like a flag would otherwise be parsed as one.
        self.assertIn("-- docker.io/library/redis:8", self.unit)

    def test_a_crashed_container_does_not_block_the_next_start(self) -> None:
        self.assertIn("--replace", self.unit)
        self.assertIn("--rm", self.unit)

    def test_it_is_wanted_by_the_default_target(self) -> None:
        # This is what a lingering user manager starts at boot.
        self.assertIn("WantedBy=default.target", self.unit)

    def test_no_privileged_or_host_mounting_flags_are_emitted(self) -> None:
        for forbidden in ("--privileged", "--network host", "--userns=host", ":/host", "-v /"):
            self.assertNotIn(forbidden, self.unit)

    def test_data_lives_in_a_named_volume_not_a_bind_mount(self) -> None:
        self.assertIn("--volume paddock-redis:/data", self.unit)

    def test_it_runs_podman_from_an_absolute_path(self) -> None:
        self.assertIn(f"ExecStart=/usr/bin/{ENGINE} run", self.unit)


class MissingEngineTests(ServiceFixture, unittest.TestCase):
    """podman is a hard dependency, so absence means someone removed it."""

    def test_configure_refuses_and_names_the_command(self) -> None:
        with self.assertRaises(ServiceError) as caught:
            self.without_engine.configure("redis")
        self.assertIn("pacman -S", str(caught.exception))

    def test_configure_writes_no_state_when_the_engine_is_missing(self) -> None:
        with self.assertRaises(ServiceError):
            self.without_engine.configure("redis")
        self.assertEqual([], self.manager.list())
        self.assertFalse(self.manager.unit_path("redis").exists())

    def test_start_refuses_early(self) -> None:
        self.manager.configure("redis")
        self.calls.clear()
        with self.assertRaises(ServiceError):
            self.without_engine.control("start", "redis")
        self.assertNotIn("enable", [part for call in self.calls for part in call])

    def test_stop_and_remove_still_work_without_the_engine(self) -> None:
        # Someone who removed podman must still be able to tidy up.
        self.manager.configure("redis")
        self.without_engine.control("stop", "redis")
        self.without_engine.remove("redis", delete_data=False)
        self.assertEqual([], self.manager.list())


class LingerTests(ServiceFixture, unittest.TestCase):
    """Without lingering a service stops at logout and misses the next boot."""

    def test_lingering_is_read_from_loginctl(self) -> None:
        manager = ServiceManager(
            self.store, recording_runner(self.calls, out="yes"),
            which=lambda _: "/usr/bin/podman",
        )
        self.assertTrue(manager.lingering())

    def test_a_disabled_linger_is_reported_as_such(self) -> None:
        manager = ServiceManager(
            self.store, recording_runner([], out="no"), which=lambda _: "/usr/bin/podman"
        )
        self.assertFalse(manager.lingering())

    def test_the_query_names_a_user(self) -> None:
        # `loginctl show-user --property=Linger --value` with no user exits 0
        # and prints nothing, so an earlier version reported every machine as
        # not lingering. Mocking stdout could not catch that; only the emitted
        # command can.
        manager = ServiceManager(
            self.store, recording_runner(self.calls, out="yes"),
            which=lambda _: "/usr/bin/podman",
        )
        manager.lingering()
        command = self.calls[-1]
        self.assertEqual(["loginctl", "show-user"], command[:2])
        self.assertEqual(str(os.getuid()), command[2])

    def test_a_missing_loginctl_is_not_fatal(self) -> None:
        # `doctor` and `report` both call this; neither may die on it.
        def missing(command, **kwargs):
            raise FileNotFoundError("loginctl")

        manager = ServiceManager(self.store, missing, which=lambda _: "/usr/bin/podman")
        self.assertFalse(manager.lingering())

    def test_an_empty_answer_is_not_read_as_enabled(self) -> None:
        manager = ServiceManager(
            self.store, recording_runner([], out=""), which=lambda _: "/usr/bin/podman"
        )
        self.assertFalse(manager.lingering())


if __name__ == "__main__":
    unittest.main()
