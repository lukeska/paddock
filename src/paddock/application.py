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

from .atomic import atomic_write, exclusive_lock
from .services import CATALOG, Service, ServiceManager
from .state import StateError, StateStore


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
    ):
        self.store = store
        self.runner = runner
        self.services = ServiceManager(store, runner, which)
        self.port_available = port_available or _port_available

    @property
    def operation_lock(self) -> Path:
        return self.store.paths.state / "redis-operation.lock"

    @staticmethod
    def redis_candidate_defaults() -> RedisConfigCandidate:
        catalog = CATALOG[REDIS]
        return RedisConfigCandidate(image=catalog.image, port=catalog.port)

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
        if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= 5000:
            return LogResult(
                False,
                "invalid_limit",
                (),
                "Log line count must be between 1 and 5000.",
            )
        if self._configured_redis() is None:
            return LogResult(False, "not_configured", (), "Redis is not configured.")
        command = [
            "journalctl",
            "--user-unit",
            "paddock-service-redis.service",
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
