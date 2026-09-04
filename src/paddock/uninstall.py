from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
from typing import Callable

from .paths import Paths


class PurgeError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class PurgePlan:
    paths: tuple[Path, ...]
    units: tuple[Path, ...]
    containers: tuple[str, ...]
    volumes: tuple[str, ...]

    @classmethod
    def discover(cls, paths: Paths) -> "PurgePlan":
        roots = tuple(dict.fromkeys((paths.config, paths.data, paths.state, paths.cache)))
        for root in roots:
            if not root.is_absolute() or root.name != "paddock":
                raise PurgeError(f"refusing unsafe purge path: {root}")
        units_dir = paths.config.parent / "systemd/user"
        units = tuple(sorted(
            path for pattern in ("paddock-*.service", "paddock-*.path", "paddock-*.target")
            for path in units_dir.glob(pattern)
        ))
        containers: set[str] = set()
        volumes: set[str] = set()
        legacy = _json(paths.config / "services.json")
        for name, record in legacy.get("services", {}).items():
            if isinstance(name, str) and isinstance(record, dict):
                containers.add(f"paddock-{name}")
                if isinstance(record.get("volume"), str):
                    volumes.add(record["volume"])
        current = _json(paths.config / "services-v2.json")
        for record in current.get("instances", {}).values():
            if not isinstance(record, dict):
                continue
            instance_id = record.get("id")
            if isinstance(instance_id, str):
                containers.add(f"paddock-{instance_id}")
            if isinstance(record.get("volume"), str):
                volumes.add(record["volume"])
        return cls(roots, units, tuple(sorted(containers)), tuple(sorted(volumes)))

    def preview(self, delete_service_data: bool) -> tuple[str, ...]:
        lines = [*(f"delete path: {path}" for path in self.paths)]
        lines.extend(f"remove user unit: {path}" for path in self.units)
        lines.extend(f"remove container: {name}" for name in self.containers)
        if self.volumes:
            action = "delete service volume" if delete_service_data else "preserve service volume"
            lines.extend(f"{action}: {name}" for name in self.volumes)
        lines.append("preserve all project source directories")
        return tuple(lines)

    def execute(self, delete_service_data: bool, runner: Runner = subprocess.run) -> None:
        for unit in self.units:
            runner(
                ["systemctl", "--user", "disable", "--now", unit.name],
                text=True, capture_output=True, check=False,
            )
        for container in self.containers:
            runner(
                ["podman", "rm", "--force", "--ignore", container],
                text=True, capture_output=True, check=False,
            )
        if delete_service_data:
            for volume in self.volumes:
                result = runner(
                    ["podman", "volume", "rm", "--force", volume],
                    text=True, capture_output=True, check=False,
                )
                if result.returncode:
                    detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
                    raise PurgeError(f"cannot delete service volume {volume}: {detail}")
        for unit in self.units:
            unit.unlink(missing_ok=True)
        if self.units:
            runner(
                ["systemctl", "--user", "daemon-reload"],
                text=True, capture_output=True, check=False,
            )
        for root in self.paths:
            if root.is_symlink():
                root.unlink()
            elif root.exists():
                shutil.rmtree(root)


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
