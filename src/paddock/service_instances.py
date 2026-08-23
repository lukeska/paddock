"""Schema-v2 multi-instance supporting services.

This module is intentionally isolated from the singleton manager while the UI,
CLI, and report consumers move to instance identities. It owns a staging
`services-v2.json` record; the final cutover makes that the canonical services
record and removes the old manager in one step.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import secrets
import socket
import subprocess
from typing import Callable

from .atomic import atomic_write, exclusive_lock
from .services import CATALOG, ENGINE, READY_TIMEOUT, ServiceError
from .state import StateStore


Runner = Callable[..., subprocess.CompletedProcess[str]]
PortAvailable = Callable[[str, int], bool]
Token = Callable[[], str]
SCHEMA_VERSION = 2
IMAGE_REFERENCE = re.compile(
    r"^[A-Za-z0-9._-]+(?::[0-9]+)?/[A-Za-z0-9._/-]+"
    r"(?::[A-Za-z0-9._-]+|@[A-Za-z0-9_+.-]+:[A-Fa-f0-9]+)$"
)


@dataclass(frozen=True)
class ServiceInstance:
    id: str
    type: str
    label: str
    image: str
    port: int
    volume: str

    @property
    def unit(self) -> str:
        return f"paddock-service-{self.id}.service"

    @property
    def container(self) -> str:
        return f"paddock-{self.id}"

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self.port}"

    def as_record(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "image": self.image,
            "port": self.port,
            "volume": self.volume,
        }


def validate_document(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "instances"}:
        raise ServiceError("service instance registry has an invalid shape")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise ServiceError(f"service instance schema must be {SCHEMA_VERSION}")
    instances = raw["instances"]
    if not isinstance(instances, dict):
        raise ServiceError("service instances must be an object")
    labels: set[str] = set()
    ports: set[int] = set()
    for instance_id, candidate in instances.items():
        if not isinstance(instance_id, str) or not instance_id:
            raise ServiceError("service instance IDs must be non-empty strings")
        if not isinstance(candidate, dict) or set(candidate) != {
            "id", "type", "label", "image", "port", "volume"
        }:
            raise ServiceError(f"service instance {instance_id} has an invalid shape")
        if candidate["id"] != instance_id:
            raise ServiceError(f"service instance {instance_id} has a mismatched ID")
        kind = candidate["type"]
        if kind not in CATALOG or not instance_id.startswith(f"{kind}-"):
            raise ServiceError(f"service instance {instance_id} has an invalid type")
        label = candidate["label"]
        if (
            not isinstance(label, str) or not label or len(label) > 80
            or any(ord(character) < 32 for character in label)
        ):
            raise ServiceError(f"service instance {instance_id} has an invalid label")
        folded = label.casefold()
        if folded in labels:
            raise ServiceError("service instance labels must be unique")
        labels.add(folded)
        port = candidate["port"]
        if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
            raise ServiceError(f"service instance {instance_id} has an invalid port")
        if port in ports:
            raise ServiceError("service instance ports must be unique")
        ports.add(port)
        for field in ("image", "volume"):
            if not isinstance(candidate[field], str) or not candidate[field]:
                raise ServiceError(f"service instance {instance_id} has an invalid {field}")
        if not IMAGE_REFERENCE.fullmatch(candidate["image"]):
            raise ServiceError(f"service instance {instance_id} has an invalid image")
    return raw


class ServiceInstanceManager:
    """Create and control independent instances without singleton assumptions."""

    def __init__(
        self,
        store: StateStore,
        runner: Runner = subprocess.run,
        *,
        port_available: PortAvailable | None = None,
        token: Token | None = None,
    ):
        self.store = store
        self.runner = runner
        self.port_available = port_available or _port_available
        self.token = token or (lambda: secrets.token_hex(4))

    @property
    def path(self) -> Path:
        return self.store.paths.config / "services-v2.json"

    @property
    def unit_directory(self) -> Path:
        return self.store.paths.config.parent / "systemd/user"

    def initialize(self) -> None:
        if self.path.exists():
            self._read()
            return
        atomic_write(
            self.path,
            b'{\n  "instances": {},\n  "schema_version": 2\n}\n',
        )

    def list(self, type: str | None = None) -> list[ServiceInstance]:
        instances = [self._instance(record) for record in self._read()["instances"].values()]
        if type is not None:
            if type not in CATALOG:
                raise ServiceError(f"unknown service type: {type}")
            instances = [instance for instance in instances if instance.type == type]
        return sorted(instances, key=lambda instance: (instance.type, instance.label.casefold()))

    def require(self, instance_id: str) -> ServiceInstance:
        record = self._read()["instances"].get(instance_id)
        if record is None:
            raise ServiceError(f"service instance does not exist: {instance_id}")
        return self._instance(record)

    def create(
        self,
        type: str,
        label: str,
        port: int | None = None,
        *,
        image: str | None = None,
    ) -> ServiceInstance:
        if type not in CATALOG:
            raise ServiceError(f"unknown service type: {type}")
        label = self._label(label)
        with exclusive_lock(self.path.with_suffix(".json.lock")):
            document = self._read()
            existing = [self._instance(value) for value in document["instances"].values()]
            self._unique_label(label, existing)
            selected_port = self._select_port(type, port, existing)
            instance_id = self._new_id(type, {item.id for item in existing})
            instance = ServiceInstance(
                instance_id,
                type,
                label,
                image or CATALOG[type].image,
                selected_port,
                f"paddock-{instance_id}",
            )
            document["instances"][instance_id] = instance.as_record()
            self._write(document)
        self.project(instance)
        return instance

    def update(self, instance_id: str, label: str, port: int) -> ServiceInstance:
        label = self._label(label)
        with exclusive_lock(self.path.with_suffix(".json.lock")):
            document = self._read()
            current_record = document["instances"].get(instance_id)
            if current_record is None:
                raise ServiceError(f"service instance does not exist: {instance_id}")
            current = self._instance(current_record)
            others = [
                self._instance(value) for key, value in document["instances"].items()
                if key != instance_id
            ]
            self._unique_label(label, others)
            if port == current.port:
                if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
                    raise ServiceError(f"port unavailable: {port}")
            else:
                self._select_port(current.type, port, others)
            updated = ServiceInstance(
                current.id, current.type, label, current.image, port, current.volume
            )
            document["instances"][instance_id] = updated.as_record()
            self._write(document)
        self.project(updated)
        return updated

    def set_autostart(self, instance_id: str, enabled: bool) -> None:
        instance = self.require(instance_id)
        if enabled:
            self.project(instance)
        self._systemctl("enable" if enabled else "disable", instance)

    def connection_lines(self, instance_id: str) -> tuple[str, ...]:
        instance = self.require(instance_id)
        return tuple(
            f"{key}={instance.port if key in {'REDIS_PORT', 'DB_PORT'} else value}"
            for key, value in CATALOG[instance.type].connection
        )

    def states_of(self, instances: list[ServiceInstance]) -> dict[str, str]:
        return self._unit_states("is-active", instances)

    def enabled_states(self, instances: list[ServiceInstance]) -> dict[str, str]:
        return self._unit_states("is-enabled", instances)

    def logs(self, instance_id: str, lines: int = 200) -> tuple[str, ...]:
        if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= 5000:
            raise ServiceError("log line count must be between 1 and 5000")
        instance = self.require(instance_id)
        result = self.runner(
            ["journalctl", "--user-unit", instance.unit, "--no-pager", "--output=cat",
             "--lines", str(lines)],
            text=True, capture_output=True, check=False,
        )
        if result.returncode:
            detail = result.stderr.strip() or "journalctl failed"
            raise ServiceError(f"cannot read {instance.label} logs: {detail}")
        return tuple((result.stdout or "").splitlines())

    def control(self, action: str, instance_id: str) -> None:
        if action not in {"start", "stop", "restart"}:
            raise ServiceError(f"unsupported service action: {action}")
        instance = self.require(instance_id)
        if action in {"start", "restart"}:
            self.project(instance)
        self._systemctl(action, instance)

    def remove(self, instance_id: str) -> ServiceInstance:
        instance = self.require(instance_id)
        self.runner(
            ["systemctl", "--user", "disable", "--now", instance.unit],
            text=True, capture_output=True, check=False,
        )
        with exclusive_lock(self.path.with_suffix(".json.lock")):
            document = self._read()
            document["instances"].pop(instance_id, None)
            self._write(document)
        (self.unit_directory / instance.unit).unlink(missing_ok=True)
        self.runner(
            [ENGINE, "volume", "rm", "--force", instance.volume],
            text=True, capture_output=True, check=False,
        )
        self._reload()
        return instance

    def project(self, instance: ServiceInstance) -> Path:
        catalog = CATALOG[instance.type]
        environment = "".join(
            f"Environment={key}={value}\n" for key, value in catalog.environment
        )
        env_flags = "".join(
            f" --env {key}={value}" for key, value in catalog.environment
        )
        unit = (
            "[Unit]\n"
            f"Description=Paddock {instance.label}\n\n"
            "[Service]\nType=notify\nNotifyAccess=all\n"
            + environment
            + f"ExecStart=/usr/bin/{ENGINE} run --replace --rm --sdnotify=conmon"
            f" --name {instance.container}"
            f" --publish 127.0.0.1:{instance.port}:{catalog.container_port}"
            f" --volume {instance.volume}:{catalog.data}{env_flags}"
            f" --pull missing -- {instance.image}\n"
            + (
                f"ExecStartPost=/usr/bin/timeout {READY_TIMEOUT} /bin/sh -c"
                f" 'until /usr/bin/{ENGINE} exec {instance.container}"
                f" {' '.join(catalog.ready)} >/dev/null 2>&1; do sleep 0.5; done'\n"
                if catalog.ready else ""
            )
            + f"ExecStop=/usr/bin/{ENGINE} stop --ignore {instance.container}\n"
            "Restart=on-failure\nRestartSec=500ms\n\n[Install]\nWantedBy=default.target\n"
        )
        path = self.unit_directory / instance.unit
        atomic_write(path, unit.encode(), mode=0o644, parent_mode=None)
        self._reload()
        return path

    def _select_port(
        self, type: str, requested: int | None, existing: list[ServiceInstance]
    ) -> int:
        used = {instance.port for instance in existing}
        if requested is not None:
            candidates = [requested]
        else:
            candidates = range(CATALOG[type].port, 65536)
        for candidate in candidates:
            if not 1024 <= candidate <= 65535:
                break
            if candidate not in used and self.port_available("127.0.0.1", candidate):
                return candidate
            if requested is not None:
                break
        raise ServiceError("no available port" if requested is None else f"port unavailable: {requested}")

    def _new_id(self, type: str, used: set[str]) -> str:
        for _ in range(32):
            candidate = f"{type}-{self.token()}"
            if candidate not in used:
                return candidate
        raise ServiceError("could not generate a unique service instance ID")

    @staticmethod
    def _label(label: str) -> str:
        label = label.strip()
        if not label or len(label) > 80 or any(ord(character) < 32 for character in label):
            raise ServiceError("display name must contain 1 to 80 printable characters")
        return label

    @staticmethod
    def _unique_label(label: str, existing: list[ServiceInstance]) -> None:
        if label.casefold() in {instance.label.casefold() for instance in existing}:
            raise ServiceError(f"display name is already used: {label}")

    def _read(self) -> dict:
        try:
            return validate_document(json.loads(self.path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            self.initialize()
            return self._read()
        except (OSError, json.JSONDecodeError) as error:
            raise ServiceError(f"cannot read service instance registry: {error}") from error

    def _write(self, document: dict) -> None:
        validated = validate_document(document)
        atomic_write(self.path, (json.dumps(validated, indent=2, sort_keys=True) + "\n").encode())

    @staticmethod
    def _instance(record: dict) -> ServiceInstance:
        return ServiceInstance(**record)

    def _systemctl(self, action: str, instance: ServiceInstance, *extra: str) -> None:
        result = self.runner(
            ["systemctl", "--user", action, *extra, instance.unit],
            text=True, capture_output=True, check=False,
        )
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise ServiceError(f"cannot {action} {instance.label}: {detail}")

    def _reload(self) -> None:
        self.runner(
            ["systemctl", "--user", "daemon-reload"],
            text=True, capture_output=True, check=False,
        )

    def _unit_states(
        self, operation: str, instances: list[ServiceInstance]
    ) -> dict[str, str]:
        if not instances:
            return {}
        result = self.runner(
            ["systemctl", "--user", operation, *(instance.unit for instance in instances)],
            text=True, capture_output=True, check=False,
        )
        lines = (result.stdout or "").splitlines()
        return {
            instance.id: (
                lines[index].strip()
                if index < len(lines) and lines[index].strip() else "unknown"
            )
            for index, instance in enumerate(instances)
        }


def _port_available(host: str, port: int) -> bool:
    try:
        with socket.socket() as candidate:
            candidate.bind((host, port))
    except OSError:
        return False
    return True
