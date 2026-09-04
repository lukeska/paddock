from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shlex
import socket
import subprocess
from typing import Callable

from .atomic import atomic_write, exclusive_lock
from .caddy import CaddyProjector
from .runtimes import RuntimeRegistry
from .state import StateStore


Runner = Callable[..., subprocess.CompletedProcess[str]]
PortAvailable = Callable[[str, int], bool]


class ReverbError(ValueError):
    pass


@dataclass(frozen=True)
class ReverbWorker:
    site: str
    root: Path
    port: int
    unit: str


def detects_reverb(root: Path) -> bool:
    """Whether a Laravel project declares Reverb as a Composer dependency."""
    for filename in ("composer.json", "composer.lock"):
        path = root / filename
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            continue
        if filename == "composer.json" and isinstance(document, dict):
            dependencies = {
                **(document.get("require") if isinstance(document.get("require"), dict) else {}),
                **(document.get("require-dev") if isinstance(document.get("require-dev"), dict) else {}),
            }
            if "laravel/reverb" in dependencies:
                return True
        if filename == "composer.lock" and isinstance(document, dict):
            packages = [
                *(document.get("packages") or []),
                *(document.get("packages-dev") or []),
            ]
            if any(isinstance(item, dict) and item.get("name") == "laravel/reverb" for item in packages):
                return True
    env = root / ".env"
    try:
        return any(
            line.strip() == "BROADCAST_CONNECTION=reverb"
            for line in env.read_text(encoding="utf-8").splitlines()
        )
    except (FileNotFoundError, OSError):
        return False


class ReverbManager:
    def __init__(
        self,
        store: StateStore,
        runner: Runner = subprocess.run,
        port_available: PortAvailable | None = None,
    ):
        self.store = store
        self.runner = runner
        self.port_available = port_available or _port_available
        self.lock = store.paths.state / "reverb-operation.lock"

    @property
    def unit_directory(self) -> Path:
        return self.store.paths.config.parent / "systemd/user"

    def available(self, site: str) -> bool:
        record = self._site(site)
        return detects_reverb(Path(record["root"]))

    def worker(self, site: str) -> ReverbWorker | None:
        record = self._site(site)
        reverb = record.get("reverb")
        if not reverb:
            return None
        return ReverbWorker(
            site, Path(record["root"]), reverb["port"], self._unit_name(site)
        )

    def configure(self, site: str) -> ReverbWorker:
        with exclusive_lock(self.lock):
            registry = self.store.read("sites")
            record = self._site(site, registry)
            if not detects_reverb(Path(record["root"])):
                raise ReverbError(
                    f"{site}.test does not have laravel/reverb installed"
                )
            if not record.get("reverb"):
                used = {
                    item["reverb"]["port"]
                    for item in registry["sites"].values()
                    if item.get("reverb")
                }
                port = self._select_port(used)
                record = {**record, "reverb": {"port": port}}
                registry["sites"][site] = record
                projector = CaddyProjector(self.store.paths, self.runner)
                candidate = projector.render(registry["sites"])
                projector.validate(candidate)
                self.store.write("sites", registry)
                projector.write(candidate)
                projector.reload()
                self._write_env(
                    Path(record["root"]), site, port, record["secured"]
                )
            worker = ReverbWorker(
                site, Path(record["root"]), record["reverb"]["port"],
                self._unit_name(site),
            )
            self._project(worker, record["php"])
            return worker

    def sync_environment(self, site: str) -> None:
        record = self._site(site)
        if record.get("reverb"):
            self._write_env(
                Path(record["root"]), site, record["reverb"]["port"],
                record["secured"],
            )

    def control(self, action: str, site: str) -> ReverbWorker:
        if action not in {"start", "stop", "restart"}:
            raise ReverbError(f"unsupported Reverb action: {action}")
        worker = self.worker(site)
        if worker is None:
            if action == "stop":
                raise ReverbError(f"Reverb is not configured for {site}.test")
            worker = self.configure(site)
        if action in {"start", "restart"}:
            record = self._site(site)
            self._project(worker, record["php"])
        self._systemctl(action, worker.unit)
        return worker

    def set_autostart(self, site: str, enabled: bool) -> None:
        worker = self.worker(site) or self.configure(site)
        self._systemctl("enable" if enabled else "disable", worker.unit)

    def reproject(self, site: str) -> None:
        """Refresh a configured worker after its site's PHP selection changes."""
        worker = self.worker(site)
        if worker is None:
            return
        was_active = self.state(site) == "active"
        self._project(worker, self._site(site)["php"])
        if was_active:
            self._systemctl("restart", worker.unit)

    def state(self, site: str) -> str:
        worker = self.worker(site)
        if worker is None:
            return "not-configured"
        result = self.runner(
            ["systemctl", "--user", "is-active", worker.unit],
            text=True, capture_output=True, check=False,
        )
        return (result.stdout or "").strip() or "unknown"

    def enabled(self, site: str) -> bool:
        worker = self.worker(site)
        if worker is None:
            return False
        result = self.runner(
            ["systemctl", "--user", "is-enabled", worker.unit],
            text=True, capture_output=True, check=False,
        )
        return result.returncode == 0 and (result.stdout or "").strip() == "enabled"

    def logs(self, site: str, lines: int = 200) -> tuple[str, ...]:
        if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= 5000:
            raise ReverbError("log line count must be between 1 and 5000")
        worker = self.worker(site)
        if worker is None:
            raise ReverbError(f"Reverb is not configured for {site}.test")
        result = self.runner(
            ["journalctl", "--user-unit", worker.unit, "--no-pager", "--output=cat", "--lines", str(lines)],
            text=True, capture_output=True, check=False,
        )
        if result.returncode:
            raise ReverbError(result.stderr.strip() or "cannot read Reverb logs")
        return tuple((result.stdout or "").splitlines())

    def remove(self, site: str) -> None:
        worker = self.worker(site)
        if worker is None:
            return
        self.runner(
            ["systemctl", "--user", "disable", "--now", worker.unit],
            text=True, capture_output=True, check=False,
        )
        (self.unit_directory / worker.unit).unlink(missing_ok=True)
        registry = self.store.read("sites")
        registry["sites"][site].pop("reverb", None)
        self.store.write("sites", registry)
        self._reload()
        self._project_caddy()

    def _project(self, worker: ReverbWorker, php_version: str) -> None:
        runtime = RuntimeRegistry(self.store).resolve(php_version)
        php_root = runtime.path.parent.parent
        arguments = (
            "-d", f"extension_dir={php_root / 'modules'}",
            str(worker.root / "artisan"), "reverb:start", "--no-interaction",
            "--host=127.0.0.1", f"--port={worker.port}",
        )
        environment = "".join(
            f"Environment={key}={shlex.quote(value)}\n"
            for key, value in (
                ("PHPRC", str(php_root / "etc/php.ini")),
                ("PHP_INI_SCAN_DIR", str(php_root / "etc/conf.d")),
            )
        )
        unit = (
            "[Unit]\n"
            f"Description=Paddock Reverb for {worker.site}.test\n"
            "After=network.target\n\n"
            "[Service]\n"
            f"WorkingDirectory={shlex.quote(str(worker.root))}\n"
            f"Environment=PATH={shlex.quote(str(php_root / 'bin') + ':/usr/local/bin:/usr/bin')}\n"
            + environment
            + f"ExecStart={shlex.join((str(runtime.path), *arguments))}\n"
            "Restart=on-failure\nRestartSec=1s\nKillSignal=SIGTERM\n\n"
            "[Install]\nWantedBy=default.target\n"
        )
        atomic_write(
            self.unit_directory / worker.unit,
            unit.encode(), mode=0o644, parent_mode=None,
        )
        self._reload()

    def _project_caddy(self) -> None:
        projector = CaddyProjector(self.store.paths, self.runner)
        candidate = projector.render(self.store.read("sites")["sites"])
        projector.validate(candidate)
        projector.write(candidate)
        projector.reload()

    def _select_port(self, used: set[int]) -> int:
        for port in range(8080, 65536):
            if port not in used and self.port_available("127.0.0.1", port):
                return port
        raise ReverbError("no available Reverb port")

    def _site(self, site: str, registry: dict | None = None) -> dict:
        try:
            return (registry or self.store.read("sites"))["sites"][site]
        except KeyError:
            raise ReverbError(f"site is not linked: {site}.test") from None

    def _systemctl(self, action: str, unit: str) -> None:
        result = self.runner(
            ["systemctl", "--user", action, unit],
            text=True, capture_output=True, check=False,
        )
        if result.returncode:
            raise ReverbError(
                result.stderr.strip() or result.stdout.strip() or f"cannot {action} Reverb"
            )

    def _reload(self) -> None:
        self.runner(
            ["systemctl", "--user", "daemon-reload"],
            text=True, capture_output=True, check=False,
        )

    @staticmethod
    def _unit_name(site: str) -> str:
        return f"paddock-reverb-{site}.service"

    @staticmethod
    def _write_env(root: Path, site: str, port: int, secured: bool) -> None:
        path = root / ".env"
        if not path.exists():
            return
        original = path.read_text(encoding="utf-8")
        desired = {
            "REVERB_SERVER_HOST": "127.0.0.1",
            "REVERB_SERVER_PORT": str(port),
            "REVERB_HOST": f"{site}.test",
            "REVERB_PORT": "443" if secured else "80",
            "REVERB_SCHEME": "https" if secured else "http",
        }
        found: set[str] = set()
        lines = []
        for line in original.splitlines():
            key = line.split("=", 1)[0].strip() if "=" in line else ""
            if key in desired and not line.lstrip().startswith("#"):
                lines.append(f"{key}={desired[key]}")
                found.add(key)
            else:
                lines.append(line)
        missing = [f"{key}={value}" for key, value in desired.items() if key not in found]
        updated = "\n".join(lines).rstrip()
        if missing:
            updated += "\n\n" + "\n".join(missing)
        atomic_write(path, (updated + "\n").encode())


def _port_available(host: str, port: int) -> bool:
    try:
        with socket.socket() as candidate:
            candidate.bind((host, port))
    except OSError:
        return False
    return True
