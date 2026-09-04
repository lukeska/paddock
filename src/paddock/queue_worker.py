from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shlex
import subprocess
from typing import Callable

from .atomic import atomic_write, exclusive_lock
from .runtimes import RuntimeRegistry
from .state import StateStore


Runner = Callable[..., subprocess.CompletedProcess[str]]


class QueueWorkerError(ValueError):
    pass


@dataclass(frozen=True)
class QueueWorker:
    site: str
    root: Path
    unit: str


def detects_laravel(root: Path) -> bool:
    if not (root / "artisan").is_file():
        return False
    composer = root / "composer.json"
    try:
        document = json.loads(composer.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return (root / "bootstrap/app.php").is_file()
    if not isinstance(document, dict):
        return False
    dependencies = {
        **(document.get("require") if isinstance(document.get("require"), dict) else {}),
        **(document.get("require-dev") if isinstance(document.get("require-dev"), dict) else {}),
    }
    return "laravel/framework" in dependencies or (root / "bootstrap/app.php").is_file()


class QueueWorkerManager:
    def __init__(self, store: StateStore, runner: Runner = subprocess.run):
        self.store = store
        self.runner = runner
        self.lock = store.paths.state / "queue-worker-operation.lock"

    @property
    def unit_directory(self) -> Path:
        return self.store.paths.config.parent / "systemd/user"

    def available(self, site: str) -> bool:
        return detects_laravel(Path(self._site(site)["root"]))

    def worker(self, site: str) -> QueueWorker | None:
        record = self._site(site)
        if record.get("queue") is None:
            return None
        return QueueWorker(site, Path(record["root"]), self._unit_name(site))

    def configure(self, site: str) -> QueueWorker:
        with exclusive_lock(self.lock):
            registry = self.store.read("sites")
            record = self._site(site, registry)
            root = Path(record["root"])
            if not detects_laravel(root):
                raise QueueWorkerError(f"{site}.test is not a detected Laravel project")
            if record.get("queue") is None:
                record = {**record, "queue": {"configured": True}}
                registry["sites"][site] = record
                self.store.write("sites", registry)
            worker = QueueWorker(site, root, self._unit_name(site))
            self._project(worker, record["php"])
            return worker

    def control(self, action: str, site: str) -> QueueWorker:
        if action not in {"start", "stop", "restart"}:
            raise QueueWorkerError(f"unsupported queue action: {action}")
        worker = self.worker(site)
        if worker is None:
            if action == "stop":
                raise QueueWorkerError(f"Queue is not configured for {site}.test")
            worker = self.configure(site)
        if action in {"start", "restart"}:
            self._project(worker, self._site(site)["php"])
        self._systemctl(action, worker.unit)
        return worker

    def set_autostart(self, site: str, enabled: bool) -> None:
        worker = self.worker(site) or self.configure(site)
        self._systemctl("enable" if enabled else "disable", worker.unit)

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
            raise QueueWorkerError("log line count must be between 1 and 5000")
        worker = self.worker(site)
        if worker is None:
            raise QueueWorkerError(f"Queue is not configured for {site}.test")
        result = self.runner(
            ["journalctl", "--user-unit", worker.unit, "--no-pager", "--output=cat", "--lines", str(lines)],
            text=True, capture_output=True, check=False,
        )
        if result.returncode:
            raise QueueWorkerError(result.stderr.strip() or "cannot read queue logs")
        return tuple((result.stdout or "").splitlines())

    def reproject(self, site: str) -> None:
        worker = self.worker(site)
        if worker is None:
            return
        was_active = self.state(site) == "active"
        self._project(worker, self._site(site)["php"])
        if was_active:
            self._systemctl("restart", worker.unit)

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
        registry["sites"][site].pop("queue", None)
        self.store.write("sites", registry)
        self._reload()

    def _project(self, worker: QueueWorker, php_version: str) -> None:
        runtime = RuntimeRegistry(self.store).resolve(php_version)
        php_root = runtime.path.parent.parent
        arguments = (
            "-d", f"extension_dir={php_root / 'modules'}",
            str(worker.root / "artisan"), "queue:work", "--no-interaction",
            "--sleep=1", "--tries=3",
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
            f"Description=Paddock queue worker for {worker.site}.test\n"
            "After=network.target\n\n"
            "[Service]\n"
            f"WorkingDirectory={shlex.quote(str(worker.root))}\n"
            f"Environment=PATH={shlex.quote(str(php_root / 'bin') + ':/usr/local/bin:/usr/bin')}\n"
            + environment
            + f"ExecStart={shlex.join((str(runtime.path), *arguments))}\n"
            "Restart=on-failure\nRestartSec=1s\nKillSignal=SIGTERM\n"
            "TimeoutStopSec=3600\n\n"
            "[Install]\nWantedBy=default.target\n"
        )
        atomic_write(
            self.unit_directory / worker.unit,
            unit.encode(), mode=0o644, parent_mode=None,
        )
        self._reload()

    def _site(self, site: str, registry: dict | None = None) -> dict:
        try:
            return (registry or self.store.read("sites"))["sites"][site]
        except KeyError:
            raise QueueWorkerError(f"site is not linked: {site}.test") from None

    def _systemctl(self, action: str, unit: str) -> None:
        result = self.runner(
            ["systemctl", "--user", action, unit],
            text=True, capture_output=True, check=False,
        )
        if result.returncode:
            raise QueueWorkerError(
                result.stderr.strip() or result.stdout.strip() or f"cannot {action} queue"
            )

    def _reload(self) -> None:
        self.runner(
            ["systemctl", "--user", "daemon-reload"],
            text=True, capture_output=True, check=False,
        )

    @staticmethod
    def _unit_name(site: str) -> str:
        return f"paddock-queue-{site}.service"
