"""Supporting services: Redis first, as rootless containers in user units.

ADR 0010 records the decisions. The short version: podman runs the container
rootless, and the *user* systemd manager owns the lifecycle. `paddock setup`
enables lingering as one of its disclosed privileged changes, which is what
makes a user unit start at boot and survive logout.

Because the unit belongs to the user who also writes its configuration, there
is no privilege boundary to defend here: the unit simply states what to run.
An earlier draft used a root-owned unit plus a packaged helper that rebuilt
every podman argument from a validated config file, because a root unit
reading a user-writable file would otherwise turn that file into root's
argument list. Rootless removes the reason for all of it.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable

from .atomic import atomic_write
from .state import StateStore


class ServiceError(ValueError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]

# `depends` in the package, so a missing podman means someone removed it.
# Paddock still checks rather than failing inside systemd, and never installs
# it: ADR 0007 confines privileged changes to setup and uninstall, and driving
# pacman from a routine command would contend for the database lock and risk a
# partial upgrade.
ENGINE = "podman"
ENGINE_HINT = f"sudo pacman -S --needed {ENGINE}"

# How long to wait for a service to accept connections before giving up. A
# first Postgres or MySQL start initialises its data directory, which is slow
# on cold storage; systemd fails the unit if this elapses, which is the honest
# outcome rather than reporting a database that is not listening.
READY_TIMEOUT = 90


@dataclass(frozen=True)
class Catalog:
    """What Paddock knows about a service the user did not have to choose."""

    image: str
    port: int
    container_port: int
    data: str
    volume: str
    # Container environment. Held here rather than in state on purpose: these
    # are facts about an image, not user preferences, and a user-writable
    # environment would be a way to reconfigure a container by editing JSON.
    environment: tuple[tuple[str, str], ...] = ()
    # What a stock Laravel `.env` should say to reach it, printed on `add`.
    connection: tuple[tuple[str, str], ...] = ()
    # Argv run inside the container to decide readiness. It must speak the
    # service's own protocol over TCP; see `render` for why nothing simpler
    # works.
    ready: tuple[str, ...] = ()


# Images are registry-qualified and tag-pinned: a bare `redis:8` resolves
# through the caller's registry search list, which is not reproducible. Every
# published port is above 1024, so rootless podman never needs a privileged
# bind.
#
# The databases run without a password, which is the local-development
# convention Herd, Valet and DBngin all follow, and is what an unedited
# Laravel `.env` expects. It is defensible only because every port is
# published on loopback alone; none of these is reachable from the network.
CATALOG: dict[str, Catalog] = {
    "redis": Catalog(
        image="docker.io/library/redis:8",
        port=6379,
        container_port=6379,
        data="/data",
        volume="paddock-redis",
        connection=(("REDIS_HOST", "127.0.0.1"), ("REDIS_PORT", "6379")),
        ready=("redis-cli", "-h", "127.0.0.1", "ping"),
    ),
    "mysql": Catalog(
        image="docker.io/library/mysql:8",
        port=3306,
        container_port=3306,
        data="/var/lib/mysql",
        volume="paddock-mysql",
        environment=(
            ("MYSQL_ALLOW_EMPTY_PASSWORD", "yes"),
            ("MYSQL_DATABASE", "laravel"),
        ),
        connection=(
            ("DB_CONNECTION", "mysql"), ("DB_HOST", "127.0.0.1"),
            ("DB_PORT", "3306"), ("DB_DATABASE", "laravel"),
            ("DB_USERNAME", "root"), ("DB_PASSWORD", ""),
        ),
        # Over TCP deliberately: the entrypoint runs a temporary server during
        # first-run initialisation with networking disabled, so a socket ping
        # would report ready while the database is still being built.
        ready=("mysqladmin", "ping", "-h", "127.0.0.1", "--silent"),
    ),
    "postgres": Catalog(
        image="docker.io/library/postgres:17",
        port=5432,
        container_port=5432,
        data="/var/lib/postgresql/data",
        volume="paddock-postgres",
        environment=(
            # The image refuses to start without one of these. `trust` is the
            # password-free equivalent, and matches the MySQL entry above.
            ("POSTGRES_HOST_AUTH_METHOD", "trust"),
            ("POSTGRES_USER", "postgres"),
            ("POSTGRES_DB", "laravel"),
        ),
        connection=(
            ("DB_CONNECTION", "pgsql"), ("DB_HOST", "127.0.0.1"),
            ("DB_PORT", "5432"), ("DB_DATABASE", "laravel"),
            ("DB_USERNAME", "postgres"), ("DB_PASSWORD", ""),
        ),
        # Same reasoning as MySQL: initdb's temporary server is socket-only.
        ready=("pg_isready", "-h", "127.0.0.1", "-U", "postgres"),
    ),
}


@dataclass(frozen=True)
class Service:
    name: str
    image: str
    port: int
    volume: str

    @property
    def unit(self) -> str:
        return f"paddock-service-{self.name}.service"

    @property
    def container(self) -> str:
        return f"paddock-{self.name}"

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self.port}"

    def as_record(self) -> dict[str, object]:
        return {"name": self.name, "image": self.image, "port": self.port, "volume": self.volume}


class ServiceManager:
    def __init__(
        self,
        store: StateStore,
        runner: Runner = subprocess.run,
        which: Callable[[str], str | None] = shutil.which,
    ):
        self.store = store
        self.runner = runner
        self.which = which

    @property
    def unit_directory(self) -> Path:
        # Sibling of Paddock's own config directory, so an XDG override moves
        # both together and a sandboxed test never touches the real manager.
        return self.store.paths.config.parent / "systemd" / "user"

    def unit_path(self, name: str) -> Path:
        return self.unit_directory / f"paddock-service-{name}.service"

    def require_engine(self) -> str:
        found = self.which(ENGINE)
        if found is None:
            raise ServiceError(
                f"{ENGINE} is not installed; supporting services need it. "
                f"Install it with: {ENGINE_HINT}"
            )
        return found

    def connection_lines(self, name: str) -> list[str]:
        """The `.env` a stock Laravel needs to reach this service.

        Printed rather than written. The file is the user's, and Paddock has
        no way to know which of several connections a project wants.
        """
        return [f"{key}={value}" for key, value in self.known(name).connection]

    def known(self, name: str) -> Catalog:
        try:
            return CATALOG[name]
        except KeyError:
            supported = ", ".join(sorted(CATALOG))
            raise ServiceError(f"unknown service: {name}; supported: {supported}") from None

    def list(self) -> list[Service]:
        records = self.store.read("services")["services"]
        return [
            Service(name=r["name"], image=r["image"], port=r["port"], volume=r["volume"])
            for _, r in sorted(records.items())
        ]

    def require(self, name: str) -> Service:
        self.known(name)
        for service in self.list():
            if service.name == name:
                return service
        raise ServiceError(f'service is not configured: {name}; run "paddock service add {name}"')

    def render(self, service: Service) -> str:
        """Render the user unit that runs one service.

        `Type=notify` with `--sdnotify=conmon` is the readiness gate ADR 0005
        requires: podman would otherwise report the unit started the moment it
        forked, so a dependent command could run before the service accepted a
        connection.

        `--replace` and `--rm` keep a container left behind by a crash from
        blocking the next start. The image is placed after `--` so a reference
        shaped like a flag cannot be read as one. `WantedBy=default.target`
        is what makes a lingering user manager start this at boot.

        `--sdnotify=conmon` only reports that the container is running. A
        database is not usable at that moment: Postgres and MySQL initialise a
        data directory on first start and accept nothing for several seconds,
        so `paddock service start postgres && php artisan migrate` fails.
        `ExecStartPost=` therefore blocks until the service answers, which is
        the readiness discipline ADR 0005 requires of PHP-FPM.

        The probe runs *inside* the container and speaks the service's own
        protocol. Connecting to the published port from the host does not
        work: podman binds that port as soon as the container starts, so the
        connection is accepted by the port forwarder while the database is
        still initialising. Measured on a cold start, that false signal
        returned in 0.6s against a Postgres that refused the next query.
        """
        catalog = self.known(service.name)
        environment = "".join(
            f"Environment={key}={value}\n" for key, value in catalog.environment
        )
        env_flags = "".join(f" --env {key}={value}" for key, value in catalog.environment)
        return (
            "[Unit]\n"
            f"Description=Paddock {service.name}\n"
            "\n"
            "[Service]\n"
            "Type=notify\n"
            "NotifyAccess=all\n"
            + environment +
            f"ExecStart=/usr/bin/{ENGINE} run --replace --rm --sdnotify=conmon"
            f" --name {service.container}"
            f" --publish 127.0.0.1:{service.port}:{catalog.container_port}"
            f" --volume {service.volume}:{catalog.data}"
            f"{env_flags}"
            f" --pull missing -- {service.image}\n"
            + (
                f"ExecStartPost=/usr/bin/timeout {READY_TIMEOUT} /bin/sh -c"
                f" 'until /usr/bin/{ENGINE} exec {service.container}"
                f" {' '.join(catalog.ready)} >/dev/null 2>&1; do sleep 0.5; done'\n"
                if catalog.ready else ""
            ) +
            f"ExecStop=/usr/bin/{ENGINE} stop --ignore {service.container}\n"
            "Restart=on-failure\n"
            "RestartSec=500ms\n"
            "\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )

    def configure(self, name: str, image: str | None = None, port: int | None = None) -> Service:
        catalog = self.known(name)
        self.require_engine()
        existing = self.store.read("services")["services"].get(name)
        service = Service(
            name=name,
            image=image or (existing or {}).get("image") or catalog.image,
            port=port or (existing or {}).get("port") or catalog.port,
            volume=(existing or {}).get("volume") or catalog.volume,
        )
        self.store.update(
            "services",
            lambda current: {
                **current,
                "services": {**current["services"], name: service.as_record()},
            },
        )
        self.project(service)
        return service

    def project(self, service: Service) -> Path:
        path = self.unit_path(service.name)
        # `~/.config/systemd/user` holds the user's own units, so leave its
        # mode alone; 0644 is the conventional mode for a unit file.
        atomic_write(path, self.render(service).encode(), mode=0o644, parent_mode=None)
        self.reload()
        return path

    def reproject(self) -> list[str]:
        """Rewrite every configured service's unit.

        Called by `paddock setup` for the same reason the Caddyfile and the FPM
        configuration are reprojected: a unit written by an older version must
        not survive an upgrade that changed what it should say.
        """
        written = []
        for service in self.list():
            self.project(service)
            written.append(service.name)
        return written

    def reload(self) -> None:
        self.runner(
            ["systemctl", "--user", "daemon-reload"], text=True, capture_output=True, check=False
        )

    def control(self, action: str, name: str) -> None:
        if action not in {"start", "stop", "restart"}:
            raise ServiceError(f"unsupported service action: {action}")
        service = self.require(name)
        # Stopping must keep working with the engine gone, so someone who
        # removed podman can still tidy up.
        if action in {"start", "restart"}:
            self.require_engine()
            self.project(service)
        # `enable` on start, so a configured service returns after a reboot
        # rather than needing to be started by hand every time.
        command = (
            ["systemctl", "--user", "enable", "--now", service.unit]
            if action == "start"
            else ["systemctl", "--user", action, service.unit]
        )
        result = self.runner(command, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise ServiceError(f"cannot {action} {name}: {detail}")

    def states_of(self, services: list[Service]) -> dict[str, str]:
        """One `systemctl` call for every service, not one per service.

        `is-active` accepts many units and answers in the order given, so
        listing N services costs one fork rather than N.
        """
        from .report import active_states

        by_unit = active_states([s.unit for s in services], self.runner, user=True)
        return {service.name: by_unit.get(service.unit, "unknown") for service in services}

    def state_of(self, service: Service) -> str:
        return self.states_of([service])[service.name]

    def lingering(self) -> bool:
        """Whether the user manager runs without a login session.

        Without this a service stops at logout and does not return at boot,
        which is the whole reason `paddock setup` enables it.

        The uid is required. `loginctl show-user --property=Linger --value`
        with no user exits 0 and prints nothing, so omitting it reported every
        machine as not lingering. The uid is used rather than `$USER`, which a
        sudo or cron context can disagree with.
        """
        try:
            result = self.runner(
                ["loginctl", "show-user", str(os.getuid()), "--property=Linger", "--value"],
                text=True, capture_output=True, check=False,
            )
        except OSError:
            # No loginctl means no logind, so nothing lingers. Reporting that
            # beats letting `doctor` and `report` die on a missing binary.
            return False
        return result.stdout.strip() == "yes"

    def remove(self, name: str, *, delete_data: bool) -> Service:
        """Forget a service, and only delete its data when told to.

        The volume outlives the service by default. A cache is cheap to lose
        and a database is not, and this surface is shared by both.
        """
        service = self.require(name)
        self.runner(
            ["systemctl", "--user", "disable", "--now", service.unit],
            text=True, capture_output=True, check=False,
        )
        self.store.update(
            "services",
            lambda current: {
                **current,
                "services": {k: v for k, v in current["services"].items() if k != name},
            },
        )
        self.unit_path(name).unlink(missing_ok=True)
        self.reload()
        if delete_data:
            self.runner(
                [ENGINE, "volume", "rm", "--force", service.volume],
                text=True, capture_output=True, check=False,
            )
        return service

    def logs(self, name: str, follow: bool = False) -> int:
        service = self.require(name)
        command = ["journalctl", "--user-unit", service.unit, "--no-pager"]
        if follow:
            command.append("--follow")
        return subprocess.call(command)
