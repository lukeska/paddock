"""Application-facing contracts shared by Paddock's CLI and native UI.

The low-level managers own state files, generated units, and subprocesses.  This
module turns those details into immutable models suitable for an interactive
client.  It deliberately starts with Redis: the first native-UI vertical slice.

Nothing in this module mutates Redis yet.  Candidate validation and planning
are pure, and :meth:`PaddockController.redis_snapshot` only observes existing
state.  Transactional apply and lifecycle results build on this contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import errno
from pathlib import Path
import re
import shutil
import socket
import subprocess
from typing import Callable

from .artifacts import ArtifactManifest, ManifestError, normalized_architecture
from .atomic import atomic_write, exclusive_lock
from .parking import ParkingManager
from .php_runtime import RuntimeInstaller
from .node_runtime import NodeInstaller, NodeManifest, NodeRegistry
from .lifecycle import Lifecycle, LifecycleError
from . import report
from .runtimes import RuntimeRegistry
from .reverb import ReverbManager, detects_reverb
from .queue_worker import QueueWorkerManager, detects_laravel
from .services import CATALOG, Service, ServiceManager
from .service_instances import ServiceInstanceManager
from .state import StateError, StateStore
from .sites import SiteManager
from .tls import SecurityManager
from . import siteconfig
from . import web
from .web import WebProjector


Runner = Callable[..., subprocess.CompletedProcess[str]]
PortAvailable = Callable[[str, int], bool]

REDIS = "redis"
REDIS_HOST = "127.0.0.1"
MIN_ROOTLESS_PORT = 1024
MAX_PORT = 65535

# This is intentionally narrower than the complete OCI reference grammar.  The
# MVP accepts registry-qualified references with either a tag or digest while
# rejecting values that could be confused with flags or split into argv.  It
# still permits registry ports, nested repositories, and digest algorithms.
IMAGE_REFERENCE = re.compile(
    r"^[A-Za-z0-9._-]+(?::[0-9]+)?/[A-Za-z0-9._/-]+"
    r"(?::[A-Za-z0-9._-]+|@[A-Za-z0-9_+.-]+:[A-Fa-f0-9]+)$"
)


def _semantic_version(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def service_image_version(image: str) -> str:
    """Return the human-facing tag or digest carried by a pinned image."""
    leaf = image.rsplit("/", 1)[-1]
    if "@" in leaf:
        return leaf.split("@", 1)[1]
    return leaf.rsplit(":", 1)[-1]


SERVICE_VERSION_PARTS = {"redis": 3, "mysql": 3, "postgres": 2, "mailpit": 3}


@dataclass(frozen=True)
class RedisConfigCandidate:
    image: str
    port: int


@dataclass(frozen=True)
class FieldError:
    field: str
    code: str
    message: str


@dataclass(frozen=True)
class Change:
    field: str
    before: object
    after: object


@dataclass(frozen=True)
class RedisApplyPlan:
    valid: bool
    errors: tuple[FieldError, ...]
    changes: tuple[Change, ...]
    runtime_effect: str
    preserves_volume: bool
    connection_changes: tuple[Change, ...]

    @property
    def changed(self) -> bool:
        return bool(self.changes)


@dataclass(frozen=True)
class RedisSnapshot:
    configured: bool
    image: str | None
    host: str
    port: int | None
    container_port: int
    volume: str | None
    unit: str
    active_state: str
    enabled_state: str
    lingering: bool
    connection: tuple[str, ...]

    @property
    def address(self) -> str | None:
        return f"{self.host}:{self.port}" if self.port is not None else None


@dataclass(frozen=True)
class OperationResult:
    """Stable shape reserved for the mutating controller milestone."""

    ok: bool
    code: str
    summary: str
    detail: str | None
    rollback: str | None
    snapshot: RedisSnapshot


@dataclass(frozen=True)
class LogResult:
    ok: bool
    code: str
    lines: tuple[str, ...]
    detail: str | None = None


@dataclass(frozen=True)
class DashboardService:
    key: str
    title: str
    group: str
    state: str
    detail: str
    configured: bool = True
    connection: tuple[str, ...] = ()
    port: int | None = None
    autostart: bool = False

    @property
    def active(self) -> bool:
        return self.state == "active"


@dataclass(frozen=True)
class DashboardSnapshot:
    services: tuple[DashboardService, ...]

    @property
    def controllable(self) -> tuple[DashboardService, ...]:
        return tuple(service for service in self.services if service.configured)

    @property
    def all_active(self) -> bool:
        return bool(self.controllable) and all(
            service.active for service in self.controllable
        )


@dataclass(frozen=True)
class DashboardOperationResult:
    ok: bool
    summary: str
    detail: str | None
    snapshot: DashboardSnapshot


@dataclass(frozen=True)
class ServiceInstanceView:
    id: str
    type: str
    label: str
    image: str
    version: str
    port: int
    volume: str
    state: str
    autostart: bool
    connection: tuple[str, ...]
    addresses: tuple[str, ...]
    dashboard_url: str | None

    @property
    def active(self) -> bool:
        return self.state == "active"


@dataclass(frozen=True)
class ServiceInstancesSnapshot:
    instances: tuple[ServiceInstanceView, ...]


@dataclass(frozen=True)
class ServiceInstanceOperationResult:
    ok: bool
    summary: str
    detail: str | None
    snapshot: ServiceInstancesSnapshot


@dataclass(frozen=True)
class LinkedSiteView:
    name: str
    host: str
    url: str
    php: str
    secured: bool
    root: str
    node: str | None = None
    reverb_available: bool = False
    reverb_configured: bool = False
    reverb_state: str = "not-configured"
    reverb_autostart: bool = False
    reverb_port: int | None = None
    queue_available: bool = False
    queue_configured: bool = False
    queue_state: str = "not-configured"
    queue_autostart: bool = False
    # Project type and the directory served, from the driver that identified
    # the project.
    type: str = "laravel"
    document_root: str = "public"
    # The user's own nginx fragment: always addressable, whether or not it
    # exists yet, so a client can offer to create it.
    custom_config: str = ""
    custom_config_present: bool = False
    # A fragment the project ships. `project_config_status` is one of none,
    # missing, pending, changed, trusted; anything but trusted means nothing
    # from the repository is being served.
    project_config: str | None = None
    project_config_status: str = "none"


def _configuration_view(configuration) -> tuple[str, bool, str | None, str]:
    if configuration is None:
        return ("", False, None, siteconfig.NONE)
    return (
        str(configuration.user),
        configuration.user_present,
        configuration.project_relative,
        configuration.status,
    )


@dataclass(frozen=True)
class LinkedSitesSnapshot:
    sites: tuple[LinkedSiteView, ...]
    php_versions: tuple[str, ...]
    node_versions: tuple[str, ...] = ()


@dataclass(frozen=True)
class LinkedSitesOperationResult:
    ok: bool
    summary: str
    detail: str | None
    snapshot: LinkedSitesSnapshot


@dataclass(frozen=True)
class ParkingSnapshot:
    paths: tuple[str, ...]
    conflicts: tuple[str, ...]


@dataclass(frozen=True)
class ParkingOperationResult:
    ok: bool
    summary: str
    detail: str | None
    snapshot: ParkingSnapshot


@dataclass(frozen=True)
class PhpVersionView:
    minor: str
    release: str
    architecture: str
    installed: bool
    available: bool
    path: str | None


@dataclass(frozen=True)
class PhpVersionsSnapshot:
    versions: tuple[PhpVersionView, ...]
    architecture: str


@dataclass(frozen=True)
class PhpInstallResult:
    ok: bool
    summary: str
    detail: str | None
    snapshot: PhpVersionsSnapshot


@dataclass(frozen=True)
class NodeVersionView:
    major: str
    release: str
    architecture: str
    installed: bool
    available: bool
    path: str | None


@dataclass(frozen=True)
class NodeVersionsSnapshot:
    versions: tuple[NodeVersionView, ...]
    architecture: str


@dataclass(frozen=True)
class NodeInstallResult:
    ok: bool
    summary: str
    detail: str | None
    snapshot: NodeVersionsSnapshot


class OperationFailure(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def validate_redis(candidate: RedisConfigCandidate) -> tuple[FieldError, ...]:
    """Validate all user-editable Redis fields without touching the machine."""
    errors: list[FieldError] = []
    image = candidate.image
    if not isinstance(image, str) or not image:
        errors.append(FieldError("image", "invalid_image", "Enter a container image."))
    elif image != image.strip() or any(character.isspace() for character in image):
        errors.append(
            FieldError("image", "invalid_image", "The container image cannot contain whitespace.")
        )
    elif image.startswith("-") or not IMAGE_REFERENCE.fullmatch(image):
        errors.append(
            FieldError(
                "image",
                "invalid_image",
                "Use a registry-qualified image with a tag or digest, such as "
                "docker.io/library/redis:8.",
            )
        )

    port = candidate.port
    if isinstance(port, bool) or not isinstance(port, int):
        errors.append(FieldError("port", "invalid_port", "The port must be an integer."))
    elif not MIN_ROOTLESS_PORT <= port <= MAX_PORT:
        errors.append(
            FieldError(
                "port",
                "invalid_port",
                f"Choose an unprivileged port between {MIN_ROOTLESS_PORT} and {MAX_PORT}.",
            )
        )
    return tuple(errors)


class PaddockController:
    """Typed application boundary for interactive Paddock clients."""

    def __init__(
        self,
        store: StateStore,
        runner: Runner = subprocess.run,
        which: Callable[[str], str | None] = shutil.which,
        port_available: PortAvailable | None = None,
        artifact_paths: tuple[Path, ...] | None = None,
        node_artifact_paths: tuple[Path, ...] | None = None,
    ):
        self.store = store
        self.runner = runner
        self.services = ServiceManager(store, runner, which)
        self.instances = ServiceInstanceManager(
            store, runner, port_available=port_available
        )
        self.parking = ParkingManager(store, runner)
        self.port_available = port_available or _port_available
        self.artifact_paths = artifact_paths or (
            Path("/usr/share/paddock/artifacts.json"),
            Path(__file__).resolve().parents[2] / "resources" / "artifacts.json",
        )
        self.node_artifact_paths = node_artifact_paths or (
            Path("/usr/share/paddock/node-artifacts.json"),
            Path(__file__).resolve().parents[2] / "resources/node-artifacts.json",
        )

    @property
    def operation_lock(self) -> Path:
        return self.store.paths.state / "redis-operation.lock"

    @staticmethod
    def redis_candidate_defaults() -> RedisConfigCandidate:
        catalog = CATALOG[REDIS]
        return RedisConfigCandidate(image=catalog.image, port=catalog.port)

    def service_instances_snapshot(self) -> ServiceInstancesSnapshot:
        instances = self.instances.list()
        states = self.instances.states_of(instances)
        enabled = self.instances.enabled_states(instances)
        return ServiceInstancesSnapshot(tuple(
            ServiceInstanceView(
                instance.id,
                instance.type,
                instance.label,
                instance.image,
                service_image_version(instance.image),
                instance.port,
                instance.volume,
                states.get(instance.id, "unknown"),
                enabled.get(instance.id) == "enabled",
                self.instances.connection_lines(instance.id),
                tuple(
                    f"127.0.0.1:{host} → {container}"
                    for host, container in self.instances.ports(instance.id)
                ),
                self.instances.dashboard_url(instance.id),
            )
            for instance in instances
        ))

    def linked_sites_snapshot(self) -> LinkedSitesSnapshot:
        """Return every linked site in stable display order without raising."""
        try:
            projector = WebProjector(self.store.paths, self.runner)
            self.parking.reconcile(projector)
            sites = SiteManager(self.store, projector).list()
        except (OSError, StateError, ValueError):
            sites = []
        try:
            versions = tuple(runtime.version for runtime in RuntimeRegistry(self.store).list())
        except (OSError, StateError, ValueError):
            versions = ()
        try: node_versions = tuple(runtime.version for runtime in NodeRegistry(self.store).list())
        except (OSError, StateError, ValueError): node_versions = ()
        reverb = ReverbManager(self.store, self.runner, self.port_available)
        queue = QueueWorkerManager(self.store, self.runner)
        try:
            configurations = SiteManager(
                self.store, WebProjector(self.store.paths, self.runner)
            ).configurations()
        except (OSError, StateError, ValueError):
            configurations = {}
        return LinkedSitesSnapshot(tuple(
            LinkedSiteView(
                site.name,
                f"{site.name}.test",
                f"{'https' if site.secured else 'http'}://{site.name}.test",
                site.php,
                site.secured,
                str(site.root),
                site.node,
                detects_reverb(site.root),
                reverb.worker(site.name) is not None,
                reverb.state(site.name),
                reverb.enabled(site.name),
                reverb.worker(site.name).port if reverb.worker(site.name) else None,
                detects_laravel(site.root),
                queue.worker(site.name) is not None,
                queue.state(site.name),
                queue.enabled(site.name),
                site.driver,
                site.document_root,
                *_configuration_view(configurations.get(site.name)),
            )
            for site in sorted(sites, key=lambda item: item.name.casefold())
        ), versions, node_versions)

    def set_reverb_active(
        self, name: str, active: bool
    ) -> LinkedSitesOperationResult:
        try:
            ReverbManager(self.store, self.runner, self.port_available).control(
                "start" if active else "stop", name
            )
        except (OSError, RuntimeError, ValueError, StateError) as error:
            return LinkedSitesOperationResult(
                False, "Reverb could not be changed", str(error),
                self.linked_sites_snapshot(),
            )
        return LinkedSitesOperationResult(
            True, f"{'Started' if active else 'Stopped'} Reverb for {name}.test",
            None, self.linked_sites_snapshot(),
        )

    def set_reverb_autostart(
        self, name: str, enabled: bool
    ) -> LinkedSitesOperationResult:
        try:
            ReverbManager(self.store, self.runner, self.port_available).set_autostart(
                name, enabled
            )
        except (OSError, RuntimeError, ValueError, StateError) as error:
            return LinkedSitesOperationResult(
                False, "Reverb autostart could not be changed", str(error),
                self.linked_sites_snapshot(),
            )
        return LinkedSitesOperationResult(
            True, f"Reverb autostart {'enabled' if enabled else 'disabled'} for {name}.test",
            None, self.linked_sites_snapshot(),
        )

    def reverb_logs(self, name: str, lines: int = 200) -> tuple[str, ...]:
        return ReverbManager(self.store, self.runner, self.port_available).logs(name, lines)

    def set_queue_active(
        self, name: str, active: bool
    ) -> LinkedSitesOperationResult:
        try:
            QueueWorkerManager(self.store, self.runner).control(
                "start" if active else "stop", name
            )
        except (OSError, RuntimeError, ValueError, StateError) as error:
            return LinkedSitesOperationResult(
                False, "Queue worker could not be changed", str(error),
                self.linked_sites_snapshot(),
            )
        return LinkedSitesOperationResult(
            True, f"{'Started' if active else 'Stopped'} queue worker for {name}.test",
            None, self.linked_sites_snapshot(),
        )

    def set_queue_autostart(
        self, name: str, enabled: bool
    ) -> LinkedSitesOperationResult:
        try:
            QueueWorkerManager(self.store, self.runner).set_autostart(name, enabled)
        except (OSError, RuntimeError, ValueError, StateError) as error:
            return LinkedSitesOperationResult(
                False, "Queue autostart could not be changed", str(error),
                self.linked_sites_snapshot(),
            )
        return LinkedSitesOperationResult(
            True, f"Queue autostart {'enabled' if enabled else 'disabled'} for {name}.test",
            None, self.linked_sites_snapshot(),
        )

    def queue_logs(self, name: str, lines: int = 200) -> tuple[str, ...]:
        return QueueWorkerManager(self.store, self.runner).logs(name, lines)

    def parking_snapshot(self) -> ParkingSnapshot:
        try:
            discovery = self.parking.discover()
            paths = tuple(str(path) for path in discovery.paths)
            conflicts = tuple(
                f"{conflict.name or conflict.roots[0]}: {conflict.reason}"
                for conflict in discovery.conflicts
            )
        except (OSError, StateError, ValueError) as error:
            return ParkingSnapshot((), (str(error),))
        return ParkingSnapshot(paths, conflicts)

    def php_versions_snapshot(self) -> PhpVersionsSnapshot:
        """Combine published runtimes for this CPU with locally installed ones."""
        architecture = normalized_architecture()
        published: dict[str, str] = {}
        for path in self.artifact_paths:
            if not path.is_file():
                continue
            try:
                manifest = ArtifactManifest.load(path)
            except ManifestError:
                continue
            for artifact in manifest.artifacts:
                if artifact.architecture == architecture:
                    current = published.get(artifact.minor)
                    if current is None or _semantic_version(artifact.php) > _semantic_version(current):
                        published[artifact.minor] = artifact.php
            break

        try:
            installed = {
                runtime.version: runtime for runtime in RuntimeRegistry(self.store).list()
            }
        except (OSError, StateError, ValueError):
            installed = {}
        versions = tuple(
            PhpVersionView(
                minor=minor,
                release=published.get(minor, minor),
                architecture=architecture,
                installed=minor in installed,
                available=minor in published,
                path=str(installed[minor].path) if minor in installed else None,
            )
            for minor in sorted(
                set(published) | set(installed), key=_semantic_version, reverse=True
            )
        )
        return PhpVersionsSnapshot(versions, architecture)

    def install_php(self, minor: str) -> PhpInstallResult:
        """Install one published runtime and return a fresh presentation model."""
        try:
            manifest = None
            for path in self.artifact_paths:
                if not path.is_file():
                    continue
                try:
                    manifest = ArtifactManifest.load(path)
                except ManifestError:
                    continue
                break
            if manifest is None:
                raise FileNotFoundError(
                    f"no PHP runtime catalog is available for {minor}"
                )
            destination = RuntimeInstaller(self.store, self.runner).install(
                minor, manifest
            )
        except (OSError, RuntimeError, ValueError, StateError) as error:
            return PhpInstallResult(
                False, f"PHP {minor} could not be installed", str(error),
                self.php_versions_snapshot(),
            )
        return PhpInstallResult(
            True, f"Installed PHP {minor}", str(destination),
            self.php_versions_snapshot(),
        )

    def node_versions_snapshot(self) -> NodeVersionsSnapshot:
        architecture = normalized_architecture()
        published: dict[str, str] = {}
        for path in self.node_artifact_paths:
            if not path.is_file(): continue
            try: manifest = NodeManifest.load(path)
            except RuntimeError: continue
            for artifact in manifest.artifacts:
                if artifact.architecture == architecture:
                    current = published.get(artifact.major)
                    if current is None or _semantic_version(artifact.node) > _semantic_version(current): published[artifact.major] = artifact.node
            break
        try: installed = {runtime.version: runtime for runtime in NodeRegistry(self.store).list()}
        except (OSError, StateError, ValueError): installed = {}
        versions = tuple(NodeVersionView(
            major, published.get(major, major), architecture, major in installed,
            major in published, str(installed[major].path) if major in installed else None,
        ) for major in sorted(set(published) | set(installed), key=int, reverse=True))
        return NodeVersionsSnapshot(versions, architecture)

    def install_node(self, major: str) -> NodeInstallResult:
        try:
            catalog = next((path for path in self.node_artifact_paths if path.is_file()), None)
            if catalog is None: raise FileNotFoundError(f"no Node runtime catalog is available for {major}")
            destination = NodeInstaller(self.store).install(major, NodeManifest.load(catalog))
        except (OSError, RuntimeError, ValueError, StateError) as error:
            return NodeInstallResult(False, f"Node {major} could not be installed", str(error), self.node_versions_snapshot())
        return NodeInstallResult(True, f"Installed Node {major}", str(destination), self.node_versions_snapshot())

    def add_parking_path(self, path: str) -> ParkingOperationResult:
        try:
            added = self.parking.add(Path(path))
            self.parking.sync_watchers()
            result = self.parking.reconcile(WebProjector(self.store.paths, self.runner))
            conflicts = "\n".join(
                f"{item.name or item.roots[0]}: {item.reason}" for item in result.conflicts
            ) or None
        except (OSError, RuntimeError, ValueError, StateError) as error:
            return ParkingOperationResult(
                False, "Parking folder could not be added", str(error),
                self.parking_snapshot(),
            )
        return ParkingOperationResult(
            not result.conflicts, f"Parked {added}", conflicts, self.parking_snapshot()
        )

    def remove_parking_path(self, path: str) -> ParkingOperationResult:
        try:
            removed = self.parking.remove(Path(path))
            self.parking.sync_watchers()
            self.parking.reconcile(WebProjector(self.store.paths, self.runner))
        except (OSError, RuntimeError, ValueError, StateError) as error:
            return ParkingOperationResult(
                False, "Parking folder could not be removed", str(error),
                self.parking_snapshot(),
            )
        return ParkingOperationResult(
            True, f"Forgot {removed}", None, self.parking_snapshot()
        )

    def set_linked_site_php(
        self, name: str, version: str
    ) -> LinkedSitesOperationResult:
        manager = SiteManager(self.store, WebProjector(self.store.paths, self.runner))
        try:
            site = next((item for item in manager.list() if item.name == name), None)
            if site is None:
                raise ValueError(f"linked site does not exist: {name}")
            manager.link(site.root, site.name, version, site.node)
        except (OSError, RuntimeError, ValueError, StateError) as error:
            return LinkedSitesOperationResult(
                False, "PHP version could not be changed", str(error),
                self.linked_sites_snapshot(),
            )
        return LinkedSitesOperationResult(
            True, f"Switched {site.name}.test to PHP {version}", None,
            self.linked_sites_snapshot(),
        )

    def set_linked_site_node(self, name: str, version: str) -> LinkedSitesOperationResult:
        manager = SiteManager(self.store, WebProjector(self.store.paths, self.runner))
        try:
            site = next((item for item in manager.list() if item.name == name), None)
            if site is None: raise ValueError(f"linked site does not exist: {name}")
            manager.link(site.root, site.name, site.php, version)
        except (OSError, RuntimeError, ValueError, StateError) as error:
            return LinkedSitesOperationResult(False, "Node version could not be changed", str(error), self.linked_sites_snapshot())
        return LinkedSitesOperationResult(True, f"Switched {site.name}.test to Node {version}", None, self.linked_sites_snapshot())

    def set_linked_site_secured(
        self, name: str, secured: bool
    ) -> LinkedSitesOperationResult:
        projector = WebProjector(self.store.paths, self.runner)
        security = SecurityManager(self.store, projector, self.runner)
        try:
            if secured:
                security.secure(name)
            else:
                security.unsecure(name)
            ReverbManager(self.store, self.runner, self.port_available).sync_environment(name)
        except (OSError, RuntimeError, ValueError, StateError) as error:
            return LinkedSitesOperationResult(
                False, "Site security could not be changed", str(error),
                self.linked_sites_snapshot(),
            )
        protocol = "HTTPS" if secured else "HTTP"
        return LinkedSitesOperationResult(
            True, f"Switched {name}.test to {protocol}", None,
            self.linked_sites_snapshot(),
        )

    def set_site_configuration_trusted(
        self, name: str, trusted: bool
    ) -> LinkedSitesOperationResult:
        """Trust or withdraw trust in the nginx fragment a project ships.

        Trust is the fragment's digest, so this is only ever granted to
        contents someone has seen. A later edit or pull withdraws it without
        anyone having to act.
        """
        manager = SiteManager(self.store, WebProjector(self.store.paths, self.runner))
        try:
            configuration = manager.trust_project_configuration(name, trusted=trusted)
        except (OSError, RuntimeError, ValueError, StateError) as error:
            return LinkedSitesOperationResult(
                False, "Project configuration could not be applied", str(error),
                self.linked_sites_snapshot(),
            )
        summary = (
            f"Applied {configuration.project_relative} for {name}.test"
            if trusted
            else f"Stopped reading {configuration.project_relative} for {name}.test"
        )
        return LinkedSitesOperationResult(
            True, summary, None, self.linked_sites_snapshot()
        )

    def ensure_site_configuration(self, name: str) -> LinkedSitesOperationResult:
        """Make the user's own fragment exist so a client can open it.

        A UI cannot offer "edit this" for a file that is not there, and it must
        not write one itself: the template explains nginx's precedence rules
        and lives with the rest of the configuration logic.
        """
        manager = SiteManager(self.store, WebProjector(self.store.paths, self.runner))
        try:
            configuration = manager.configuration(name)
            if not configuration.user.exists():
                atomic_write(configuration.user, siteconfig.template(name).encode())
                manager.reproject()
        except (OSError, RuntimeError, ValueError, StateError) as error:
            return LinkedSitesOperationResult(
                False, "Site configuration could not be prepared", str(error),
                self.linked_sites_snapshot(),
            )
        return LinkedSitesOperationResult(
            True, f"Ready to edit {configuration.user}", str(configuration.user),
            self.linked_sites_snapshot(),
        )

    def reload_web(self) -> DashboardOperationResult:
        """Re-generate and reload, which is how a fragment edited outside
        Paddock takes effect and how its mistakes are surfaced."""
        manager = SiteManager(self.store, WebProjector(self.store.paths, self.runner))
        try:
            manager.reproject()
        except (OSError, RuntimeError, ValueError, StateError) as error:
            return DashboardOperationResult(
                False, "The web configuration was rejected", str(error),
                self.dashboard_snapshot(),
            )
        return DashboardOperationResult(
            True, "Reloaded the web configuration", None, self.dashboard_snapshot()
        )

    def create_service_instance(
        self, type: str, label: str, port: int | None, autostart: bool
    ) -> ServiceInstanceOperationResult:
        try:
            instance = self.instances.create(type, label, port)
            self.instances.set_autostart(instance.id, autostart)
            self.instances.control("start", instance.id)
        except (OSError, RuntimeError, ValueError) as error:
            return ServiceInstanceOperationResult(
                False, "Service could not be added", str(error),
                self.service_instances_snapshot(),
            )
        return ServiceInstanceOperationResult(
            True, f"Added and started {instance.label}", None,
            self.service_instances_snapshot()
        )

    def set_service_instance_active(
        self, instance_id: str, active: bool
    ) -> ServiceInstanceOperationResult:
        try:
            instance = self.instances.require(instance_id)
            self.instances.control("start" if active else "stop", instance_id)
        except (OSError, RuntimeError, ValueError) as error:
            return ServiceInstanceOperationResult(
                False, "Service state could not be changed", str(error),
                self.service_instances_snapshot(),
            )
        return ServiceInstanceOperationResult(
            True, f"{'Started' if active else 'Stopped'} {instance.label}", None,
            self.service_instances_snapshot(),
        )

    def update_service_instance(
        self, instance_id: str, label: str, port: int, autostart: bool
    ) -> ServiceInstanceOperationResult:
        try:
            before = self.instances.require(instance_id)
            was_active = self.instances.states_of([before]).get(instance_id) == "active"
            port_changed = before.port != port
            updated = self.instances.update(instance_id, label, port)
            self.instances.set_autostart(instance_id, autostart)
            if was_active and port_changed:
                self.instances.control("restart", instance_id)
        except (OSError, RuntimeError, ValueError) as error:
            return ServiceInstanceOperationResult(
                False, "Service settings could not be saved", str(error),
                self.service_instances_snapshot(),
            )
        return ServiceInstanceOperationResult(
            True, f"Saved {updated.label} settings", None,
            self.service_instances_snapshot(),
        )

    def remove_service_instance(self, instance_id: str) -> ServiceInstanceOperationResult:
        try:
            removed = self.instances.remove(instance_id)
        except (OSError, RuntimeError, ValueError) as error:
            return ServiceInstanceOperationResult(
                False, "Service could not be removed", str(error),
                self.service_instances_snapshot(),
            )
        return ServiceInstanceOperationResult(
            True, f"Removed {removed.label} and its data", None,
            self.service_instances_snapshot(),
        )

    def service_instance_logs(self, instance_id: str, lines: int = 200) -> LogResult:
        try:
            return LogResult(True, "ok", self.instances.logs(instance_id, lines))
        except (OSError, RuntimeError, ValueError) as error:
            return LogResult(False, "logs_failed", (), str(error))

    def redis_snapshot(self) -> RedisSnapshot:
        """Return one non-raising Redis view for the CLI or native app.

        A broken service record is represented as unavailable instead of
        crashing the UI.  Once configured, systemd's states are kept verbatim
        so activating, deactivating, failed, and unknown remain distinguishable.
        """
        catalog = CATALOG[REDIS]
        service = self._configured_redis()
        lingering = self.services.lingering()
        if service is None:
            return RedisSnapshot(
                configured=False,
                image=None,
                host=REDIS_HOST,
                port=None,
                container_port=catalog.container_port,
                volume=None,
                unit=Service(REDIS, catalog.image, catalog.port, catalog.volume).unit,
                active_state="not-configured",
                enabled_state="not-configured",
                lingering=lingering,
                connection=(),
            )

        return RedisSnapshot(
            configured=True,
            image=service.image,
            host=REDIS_HOST,
            port=service.port,
            container_port=catalog.container_port,
            volume=service.volume,
            unit=service.unit,
            active_state=self.services.state_of(service),
            enabled_state=self._enabled_state(service.unit),
            lingering=lingering,
            connection=(f"REDIS_HOST={REDIS_HOST}", f"REDIS_PORT={service.port}"),
        )

    def dashboard_snapshot(self) -> DashboardSnapshot:
        """Return the services a user can understand and control from the UI."""
        payload = report.build(self.store, self.runner)
        units = {unit["name"]: unit["state"] for unit in payload["units"]}
        services: list[DashboardService] = [
            DashboardService(
                "web",
                "Web",
                "Infrastructure",
                units.get(web.UNIT, "unknown"),
                "Nginx · HTTPS and .test sites",
            ),
            DashboardService(
                "dns",
                "DNS",
                "Infrastructure",
                units.get("paddock-dns.service", "unknown"),
                "Local .test domain resolution",
            ),
        ]
        services.extend(
            DashboardService(
                f"php-{runtime['minor']}",
                f"PHP {runtime['minor']}",
                "PHP",
                runtime["state"],
                (
                    f"PHP {runtime['release']} · FPM"
                    if runtime["release"] else "PHP-FPM runtime"
                ),
            )
            for runtime in payload["php"]["runtimes"]
        )
        for instance in self.service_instances_snapshot().instances:
            services.append(
                DashboardService(
                    instance.id,
                    instance.label,
                    "Services",
                    instance.state,
                    f"{instance.version} · Port: {instance.port}",
                    connection=instance.connection,
                    port=instance.port,
                    autostart=instance.autostart,
                )
            )
        return DashboardSnapshot(tuple(services))

    def save_service_settings(
        self, name: str, label: str, port: int, autostart: bool
    ) -> DashboardOperationResult:
        """Persist a UI label and safely apply the only editable runtime setting."""
        label = label.strip()
        if name not in CATALOG:
            return DashboardOperationResult(
                False, "Unknown service", name, self.dashboard_snapshot()
            )
        if not label or len(label) > 80 or any(ord(character) < 32 for character in label):
            return DashboardOperationResult(
                False,
                "Invalid display name",
                "Enter between 1 and 80 printable characters.",
                self.dashboard_snapshot(),
            )
        if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
            return DashboardOperationResult(
                False,
                "Invalid port",
                "Choose an unprivileged port between 1024 and 65535.",
                self.dashboard_snapshot(),
            )

        configured = {service.name: service for service in self.services.list()}
        conflict = next(
            (
                service
                for service in configured.values()
                if service.name != name and service.port == port
            ),
            None,
        )
        if conflict is not None:
            return DashboardOperationResult(
                False,
                "Port is already used by Paddock",
                f"{conflict.name} already uses port {port}.",
                self.dashboard_snapshot(),
            )

        current = configured.get(name)
        port_changed = current is None or current.port != port
        if port_changed and not self.port_available(REDIS_HOST, port):
            return DashboardOperationResult(
                False,
                "Port is unavailable",
                f"Another process is already listening on {REDIS_HOST}:{port}.",
                self.dashboard_snapshot(),
            )

        try:
            if port_changed:
                if name == REDIS:
                    image = current.image if current else CATALOG[name].image
                    result = self.apply_redis(
                        RedisConfigCandidate(image, port), start_after_add=False
                    )
                    if not result.ok:
                        return DashboardOperationResult(
                            False, result.summary, result.detail, self.dashboard_snapshot()
                        )
                else:
                    was_active = current is not None and self.services.state_of(current) == "active"
                    self.services.configure(name, port=port)
                    if was_active:
                        self.services.control("restart", name)

            self.services.set_autostart(name, autostart)

            self.store.update(
                "settings",
                lambda settings: {
                    **settings,
                    "service_labels": {
                        **settings.get("service_labels", {}),
                        name: label,
                    },
                },
            )
        except (OSError, RuntimeError, ValueError, StateError) as error:
            return DashboardOperationResult(
                False,
                "Service settings could not be saved",
                str(error),
                self.dashboard_snapshot(),
            )

        return DashboardOperationResult(
            True,
            f"Saved {label} settings",
            None,
            self.dashboard_snapshot(),
        )

    def _enabled_states(self, units: list[str]) -> dict[str, str]:
        if not units:
            return {}
        try:
            result = self.runner(
                ["systemctl", "--user", "is-enabled", *units],
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError:
            return {unit: "unknown" for unit in units}
        lines = (result.stdout or "").splitlines()
        return {
            unit: lines[index].strip() if index < len(lines) else "unknown"
            for index, unit in enumerate(units)
        }

    def set_service_active(self, name: str, active: bool) -> DashboardOperationResult:
        """Configure when necessary, then start or temporarily stop one service."""
        if name not in CATALOG:
            return DashboardOperationResult(
                False,
                f"Unknown service: {name}",
                "Choose a service from Paddock's catalog.",
                self.dashboard_snapshot(),
            )

        try:
            configured = any(service.name == name for service in self.services.list())
            if name == REDIS:
                if active and not configured:
                    result = self.apply_redis(self.redis_candidate_defaults())
                else:
                    result = (self.start_redis() if active else self.stop_redis())
                return DashboardOperationResult(
                    result.ok, result.summary, result.detail, self.dashboard_snapshot()
                )

            if active and not configured:
                self.services.configure(name)
            self.services.control("start" if active else "stop", name)
        except (OSError, RuntimeError, ValueError) as error:
            action = "start" if active else "stop"
            return DashboardOperationResult(
                False,
                f"Could not {action} {name}",
                str(error),
                self.dashboard_snapshot(),
            )

        title = {
            "mysql": "MySQL", "postgres": "PostgreSQL", "redis": "Redis",
            "mailpit": "Mailpit",
        }[name]
        return DashboardOperationResult(
            True,
            f"{'Started' if active else 'Stopped'} {title}",
            None,
            self.dashboard_snapshot(),
        )

    def set_dashboard_active(self, active: bool) -> DashboardOperationResult:
        """Start or temporarily stop every configured Paddock service."""
        action = "start" if active else "stop"
        failures: list[str] = []
        with exclusive_lock(self.operation_lock):
            configured = self.instances.list()
            if active:
                try:
                    Lifecycle(self.runner).control("start")
                except (LifecycleError, OSError, ValueError) as error:
                    failures.append(str(error))
            for service in configured:
                try:
                    self.instances.control(action, service.id)
                except (OSError, RuntimeError, ValueError) as error:
                    failures.append(f"{service.label}: {error}")
            if not active:
                try:
                    Lifecycle(self.runner).control("stop")
                except (LifecycleError, OSError, ValueError) as error:
                    failures.append(str(error))

        snapshot = self.dashboard_snapshot()
        verb = "Started" if active else "Stopped"
        if failures:
            return DashboardOperationResult(
                False,
                f"{verb} some Paddock services",
                "\n".join(failures),
                snapshot,
            )
        return DashboardOperationResult(
            True, f"{verb} all configured services", None, snapshot
        )

    def validate_redis(self, candidate: RedisConfigCandidate) -> tuple[FieldError, ...]:
        return validate_redis(candidate)

    def plan_redis(self, candidate: RedisConfigCandidate) -> RedisApplyPlan:
        """Describe a candidate's durable and runtime effects without mutation."""
        errors = self.validate_redis(candidate)
        if errors:
            return RedisApplyPlan(
                valid=False,
                errors=errors,
                changes=(),
                runtime_effect="none",
                preserves_volume=True,
                connection_changes=(),
            )

        snapshot = self.redis_snapshot()
        if not snapshot.configured:
            changes = (
                Change("image", None, candidate.image),
                Change("port", None, candidate.port),
            )
            connection_changes = (
                Change("REDIS_HOST", None, REDIS_HOST),
                Change("REDIS_PORT", None, str(candidate.port)),
            )
            runtime_effect = "configure"
        else:
            changes_list: list[Change] = []
            if snapshot.image != candidate.image:
                changes_list.append(Change("image", snapshot.image, candidate.image))
            if snapshot.port != candidate.port:
                changes_list.append(Change("port", snapshot.port, candidate.port))
            changes = tuple(changes_list)
            connection_changes = (
                (Change("REDIS_PORT", str(snapshot.port), str(candidate.port)),)
                if snapshot.port != candidate.port else ()
            )
            if not changes:
                runtime_effect = "none"
            elif snapshot.active_state in {"active", "activating", "reloading"}:
                runtime_effect = "restart"
            else:
                runtime_effect = "next-start"

        return RedisApplyPlan(
            valid=True,
            errors=(),
            changes=changes,
            runtime_effect=runtime_effect,
            preserves_volume=True,
            connection_changes=connection_changes,
        )

    def apply_redis(
        self, candidate: RedisConfigCandidate, *, start_after_add: bool = True
    ) -> OperationResult:
        """Apply Redis configuration as one recoverable operation.

        State and the projected unit are restored together when projection or
        activation fails.  The separate operation lock serializes GUI, CLI,
        and project-init clients across processes, while each state write keeps
        its existing record lock and atomic replacement guarantees.
        """
        with exclusive_lock(self.operation_lock):
            plan = self.plan_redis(candidate)
            if not plan.valid:
                detail = "; ".join(error.message for error in plan.errors)
                return self._result(
                    False,
                    plan.errors[0].code,
                    "Redis configuration is invalid",
                    detail,
                )
            if not plan.changed:
                return self._result(True, "unchanged", "Redis is already configured this way")

            before_snapshot = self.redis_snapshot()
            if (
                candidate.port != before_snapshot.port
                and not self.port_available(REDIS_HOST, candidate.port)
            ):
                return self._result(
                    False,
                    "port_in_use",
                    f"Port {candidate.port} is already in use",
                    f"Choose another loopback port for Redis.",
                )

            try:
                self.services.require_engine()
            except (OSError, ValueError) as error:
                return self._result(False, "engine_missing", "Podman is unavailable", str(error))

            old_state = self.store.read("services")
            unit_path = self.services.unit_path(REDIS)
            old_unit = unit_path.read_bytes() if unit_path.exists() else None
            catalog = CATALOG[REDIS]
            service = Service(
                REDIS,
                candidate.image,
                candidate.port,
                before_snapshot.volume or catalog.volume,
            )

            try:
                self._write_service(service)
                self._write_unit(service)
                self._reload()
                if before_snapshot.active_state in {"active", "activating", "reloading"}:
                    self._systemctl("restart", service.unit)
                elif not before_snapshot.configured and start_after_add:
                    self._systemctl("enable", "--now", service.unit)
            except (OSError, RuntimeError, ValueError) as error:
                rollback = self._rollback(
                    old_state,
                    unit_path,
                    old_unit,
                    was_active=before_snapshot.active_state
                    in {"active", "activating", "reloading"},
                    unit=service.unit,
                )
                code = (
                    "apply_failed_rolled_back"
                    if rollback == "succeeded"
                    else "apply_failed_rollback_failed"
                )
                return self._result(
                    False,
                    code,
                    "Redis configuration could not be applied",
                    str(error),
                    rollback,
                )

            action = "Configured" if not before_snapshot.configured else "Updated"
            return self._result(True, "ok", f"{action} Redis")

    def start_redis(self) -> OperationResult:
        return self._control_redis("start")

    def stop_redis(self) -> OperationResult:
        return self._control_redis("stop")

    def restart_redis(self) -> OperationResult:
        return self._control_redis("restart")

    def redis_logs(self, lines: int = 200) -> LogResult:
        return self.service_logs(REDIS, lines)

    def service_logs(self, name: str, lines: int = 200) -> LogResult:
        if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= 5000:
            return LogResult(
                False,
                "invalid_limit",
                (),
                "Log line count must be between 1 and 5000.",
            )
        try:
            service = self.services.require(name)
        except (ValueError, StateError) as error:
            return LogResult(False, "not_configured", (), str(error))
        command = [
            "journalctl",
            "--user-unit",
            service.unit,
            "--no-pager",
            "--output=cat",
            "--lines",
            str(lines),
        ]
        try:
            result = self.runner(command, text=True, capture_output=True, check=False)
        except OSError as error:
            return LogResult(False, "logs_failed", (), str(error))
        output = tuple((result.stdout or "").splitlines())
        if result.returncode != 0:
            detail = (result.stderr or "").strip() or "journalctl failed"
            return LogResult(False, "logs_failed", output, detail)
        return LogResult(True, "ok", output)

    def remove_redis(self, *, delete_data: bool = False) -> OperationResult:
        with exclusive_lock(self.operation_lock):
            service = self._configured_redis()
            if service is None:
                return self._result(False, "not_configured", "Redis is not configured")
            result = self.runner(
                ["systemctl", "--user", "disable", "--now", service.unit],
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                return self._result(
                    False,
                    "systemd_failed",
                    "Redis could not be stopped for removal",
                    self._command_detail(result),
                )
            self.store.update(
                "services",
                lambda current: {
                    **current,
                    "services": {
                        key: value
                        for key, value in current["services"].items()
                        if key != REDIS
                    },
                },
            )
            self.services.unit_path(REDIS).unlink(missing_ok=True)
            try:
                self._reload()
            except OperationFailure as error:
                return self._result(
                    False,
                    error.code,
                    "Redis was removed but systemd reload failed",
                    str(error),
                )
            if delete_data:
                deleted = self.runner(
                    ["podman", "volume", "rm", "--force", service.volume],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if deleted.returncode != 0:
                    return self._result(
                        False,
                        "volume_delete_failed",
                        "Redis was removed but its data remains",
                        self._command_detail(deleted),
                    )
            return self._result(True, "ok", "Removed Redis")

    def _control_redis(self, action: str) -> OperationResult:
        with exclusive_lock(self.operation_lock):
            service = self._configured_redis()
            if service is None:
                return self._result(False, "not_configured", "Redis is not configured")
            try:
                if action in {"start", "restart"}:
                    self.services.require_engine()
                    self._write_unit(service)
                    self._reload()
                if action == "start":
                    self._systemctl("enable", "--now", service.unit)
                else:
                    self._systemctl(action, service.unit)
            except (OSError, RuntimeError, ValueError) as error:
                code = error.code if isinstance(error, OperationFailure) else "systemd_failed"
                return self._result(False, code, f"Could not {action} Redis", str(error))
            completed = {"start": "Started", "stop": "Stopped", "restart": "Restarted"}
            return self._result(True, "ok", f"{completed[action]} Redis")

    def _write_service(self, service: Service) -> None:
        self.store.update(
            "services",
            lambda current: {
                **current,
                "services": {**current["services"], REDIS: service.as_record()},
            },
        )

    def _write_unit(self, service: Service) -> None:
        atomic_write(
            self.services.unit_path(REDIS),
            self.services.render(service).encode(),
            mode=0o644,
            parent_mode=None,
        )

    def _reload(self) -> None:
        result = self.runner(
            ["systemctl", "--user", "daemon-reload"],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise OperationFailure("systemd_failed", self._command_detail(result))

    def _systemctl(self, *arguments: str) -> None:
        result = self.runner(
            ["systemctl", "--user", *arguments],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            detail = self._command_detail(result)
            code = "readiness_timeout" if "timed out" in detail.lower() else "systemd_failed"
            raise OperationFailure(code, detail)

    def _rollback(
        self,
        old_state: dict[str, object],
        unit_path: Path,
        old_unit: bytes | None,
        *,
        was_active: bool,
        unit: str,
    ) -> str:
        try:
            self.store.write("services", old_state)
            if old_unit is None:
                unit_path.unlink(missing_ok=True)
            else:
                atomic_write(unit_path, old_unit, mode=0o644, parent_mode=None)
            self._reload()
            if was_active:
                self._systemctl("restart", unit)
            return "succeeded"
        except (OSError, RuntimeError, ValueError):
            return "failed"

    def _result(
        self,
        ok: bool,
        code: str,
        summary: str,
        detail: str | None = None,
        rollback: str | None = None,
    ) -> OperationResult:
        return OperationResult(ok, code, summary, detail, rollback, self.redis_snapshot())

    @staticmethod
    def _command_detail(result: subprocess.CompletedProcess[str]) -> str:
        return (result.stderr or "").strip() or (result.stdout or "").strip() or "unknown error"

    def _configured_redis(self) -> Service | None:
        try:
            return next(
                (service for service in self.services.list() if service.name == REDIS), None
            )
        except (StateError, ValueError):
            return None

    def _enabled_state(self, unit: str) -> str:
        try:
            result = self.runner(
                ["systemctl", "--user", "is-enabled", unit],
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError:
            return "unknown"
        value = (result.stdout or "").strip().splitlines()
        return value[0] if value and value[0] else "unknown"


def _port_available(host: str, port: int) -> bool:
    """Whether an exact loopback TCP port can be bound right now."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind((host, port))
    except OSError as error:
        if error.errno in {errno.EADDRINUSE, errno.EACCES}:
            return False
        raise
    finally:
        listener.close()
    return True
