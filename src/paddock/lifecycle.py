from __future__ import annotations

import os
import subprocess
from typing import Callable


class LifecycleError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


class Lifecycle:
    def __init__(self, runner: Runner = subprocess.run):
        self.runner = runner

    def control(self, action: str) -> None:
        if action not in {"start", "stop", "restart"}:
            raise LifecycleError(f"unsupported lifecycle action: {action}")
        result = self.runner(
            ["systemctl", action, "paddock.target"],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise LifecycleError(f"cannot {action} Paddock: {detail}")

    def logs(self, follow: bool = False) -> int:
        command = [
            "journalctl",
            "--unit", "paddock-caddy.service",
            "--unit", "paddock-php@*.service",
            "--unit", "paddock-dns.service",
            "--no-pager",
        ]
        if follow:
            command.append("--follow")
        return subprocess.call(command)
