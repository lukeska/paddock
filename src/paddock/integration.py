from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Callable

from .caddy import CaddyProjector
from .php_runtime import RuntimeInstaller
from .state import StateStore


SYSTEM_HELPER = Path("/usr/lib/paddock/system-helper")


class IntegrationError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


INSTALL_CHANGES = (
    "install Paddock-only systemd target, DNS, Caddy, and PHP-FPM units",
    "add a NetworkManager dummy connection routing only ~test to 127.0.0.1",
    "trust the Paddock public CA in system and current-user NSS stores",
    "allow the desktop user to manage only Paddock systemd units",
    "enable and start paddock.target at boot",
)

REMOVE_CHANGES = (
    "stop and disable paddock.target",
    "remove the Paddock NetworkManager connection and systemd units",
    "remove only the matching Paddock CA trust entries",
    "remove the Paddock-specific policy rule and DNS configuration",
    "preserve projects, configuration, runtimes, logs, cache, and private CA",
)


class Integration:
    def __init__(self, store: StateStore, runner: Runner = subprocess.run):
        self.store = store
        self.runner = runner

    def prepare(self) -> None:
        self.store.initialize()
        for directory in (
            self.store.paths.state / "caddy-data",
            self.store.paths.state / "caddy-config",
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
        # Always reproject. The Caddyfile is derived from durable site records
        # and the socket layout, so keeping a stale generation would point
        # Caddy at sockets the current units never bind. Validation still runs
        # first, so an invalid render never replaces the last-known-good file.
        projector = CaddyProjector(self.store.paths, self.runner)
        candidate = projector.render(self.store.read("sites")["sites"])
        projector.validate(candidate)
        projector.write(candidate)
        RuntimeInstaller(self.store, self.runner).reproject()
        self._ensure_ca()

    def install(self) -> None:
        self._helper("install")

    def uninstall(self) -> None:
        self._helper("uninstall")

    def _ensure_ca(self) -> None:
        caroot = self.store.paths.data / "pki"
        root = caroot / "rootCA.pem"
        if root.exists():
            return
        caroot.mkdir(parents=True, exist_ok=True, mode=0o700)
        bootstrap = caroot / ".bootstrap.pem"
        bootstrap_key = caroot / ".bootstrap-key.pem"
        try:
            result = self.runner(
                [
                    "mkcert", "-cert-file", str(bootstrap), "-key-file",
                    str(bootstrap_key), "paddock-bootstrap.invalid",
                ],
                env={**os.environ, "CAROOT": str(caroot)},
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0 or not root.is_file():
                detail = result.stderr.strip() or result.stdout.strip() or "root CA not created"
                raise IntegrationError(f"cannot create Paddock CA: {detail}")
        finally:
            bootstrap.unlink(missing_ok=True)
            bootstrap_key.unlink(missing_ok=True)

    def _helper(self, action: str) -> None:
        if action not in {"install", "uninstall"}:
            raise IntegrationError(f"unsupported integration action: {action}")
        result = self.runner(
            [
                "sudo", str(SYSTEM_HELPER), action, "--user", os.environ["USER"],
                "--data-dir", str(self.store.paths.data),
                "--state-dir", str(self.store.paths.state),
            ],
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise IntegrationError(f"system integration {action} failed")
