"""Automatically reconcile nginx when a site fragment changes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import time
from typing import Callable

from .atomic import atomic_write
from .paths import Paths
from .sites import SiteManager
from .state import StateStore
from .web import WebProjector


class ConfigWatcherError(RuntimeError):
    pass


class ConfigWatcher:
    def __init__(self, store: StateStore, runner=subprocess.run):
        self.store = store
        self.runner = runner

    @property
    def unit_directory(self) -> Path:
        return self.store.paths.config.parent / "systemd/user"

    @property
    def unit_path(self) -> Path:
        return self.unit_directory / "paddock-nginx-config-watcher.service"

    @property
    def status_path(self) -> Path:
        return self.store.paths.state / "nginx-config-watcher.json"

    def sources(self) -> dict[str, str]:
        """Return the content digest of every input to the nginx projection."""
        paths = [self.store.path_for("sites")]
        paths.extend(sorted((self.store.paths.config / "nginx").glob("*.custom.conf")))
        try:
            records = self.store.read("sites")["sites"]
        except Exception:
            records = {}
        for _name, record in sorted(records.items()):
            relative = (record.get("nginx") or {}).get("path")
            if relative:
                paths.append(Path(record["root"]) / relative)
        return {str(path): _file_digest(path) for path in paths}

    def install(self) -> Path:
        unit = (
            "[Unit]\nDescription=Watch Paddock nginx configuration fragments\n\n"
            "[Service]\nType=simple\n"
            "ExecStart=/usr/bin/python -m paddock.config_watcher\n"
            "Restart=on-failure\nRestartSec=1s\n\n"
            "[Install]\nWantedBy=default.target\n"
        )
        atomic_write(self.unit_path, unit.encode(), mode=0o644, parent_mode=None)
        self.runner(["systemctl", "--user", "daemon-reload"], text=True, capture_output=True, check=False)
        result = self.runner(
            ["systemctl", "--user", "enable", "--now", self.unit_path.name],
            text=True, capture_output=True, check=False,
        )
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise ConfigWatcherError(f"cannot enable nginx configuration watcher: {detail}")
        return self.unit_path

    def remove(self) -> None:
        self.runner(
            ["systemctl", "--user", "disable", "--now", self.unit_path.name],
            text=True, capture_output=True, check=False,
        )
        self.unit_path.unlink(missing_ok=True)
        self.runner(["systemctl", "--user", "daemon-reload"], text=True, capture_output=True, check=False)

    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        for path, content_digest in sorted(self.sources().items()):
            digest.update(path.encode())
            digest.update(content_digest.encode())
        return digest.hexdigest()

    def run(self, *, interval: float = 0.25, debounce: float = 0.2) -> None:
        previous_sources = self.sources()
        previous = self.fingerprint()
        manager = SiteManager(self.store, WebProjector(self.store.paths, self.runner))
        while True:
            time.sleep(interval)
            previous, _changed, previous_sources = self.reconcile_if_changed(
                previous, manager, debounce=debounce, previous_sources=previous_sources
            )

    def reconcile_if_changed(
        self,
        previous: str,
        manager,
        *,
        debounce: float = 0.2,
        sleeper: Callable[[float], None] = time.sleep,
        previous_sources: dict[str, str] | None = None,
    ):
        current = self.fingerprint()
        if current == previous:
            result = (previous, False)
            return (*result, previous_sources) if previous_sources is not None else result
        sleeper(debounce)
        current = self.fingerprint()
        current_sources = self.sources()
        changed_paths = sorted(
            path for path in set(previous_sources or {}) | set(current_sources)
            if (previous_sources or {}).get(path) != current_sources.get(path)
        )
        try:
            manager.reproject()
            self.status_path.unlink(missing_ok=True)
            print("Applied changed nginx configuration", flush=True)
        except Exception as error:
            # Keep watching: the next save is the user's correction. The
            # projector leaves the last valid generation active.
            payload = json.dumps({"paths": changed_paths, "error": str(error)}, indent=2).encode()
            atomic_write(self.status_path, payload, mode=0o600, parent_mode=None)
            print(f"Could not apply changed nginx configuration: {error}", flush=True)
        result = (current, True)
        return (*result, current_sources) if previous_sources is not None else result


def _update_digest(digest, path: Path) -> None:
    digest.update(str(path).encode())
    try:
        digest.update(path.read_bytes())
    except OSError:
        digest.update(b"\0missing")


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    _update_digest(digest, path)
    return digest.hexdigest()


def main() -> None:
    store = StateStore(Paths.from_environment())
    store.initialize()
    ConfigWatcher(store).run()


if __name__ == "__main__":
    main()
