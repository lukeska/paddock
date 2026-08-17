from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable

from .atomic import atomic_write
from .paths import PathConfigurationError, Paths


class CaddyError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


class CaddyProjector:
    def __init__(self, paths: Paths, runner: Runner = subprocess.run):
        self.paths = paths
        self.runner = runner

    @property
    def path(self) -> Path:
        return self.paths.state / "caddy" / "Caddyfile"

    def render(self, sites: dict[str, dict[str, Any]]) -> str:
        if self.paths.runtime is None:
            raise PathConfigurationError("XDG_RUNTIME_DIR is required for Caddy projection")
        lines = [
            "{",
            "\tadmin 127.0.0.1:20195",
            "\tauto_https off",
            "}",
            "",
        ]
        for name, site in sorted(sites.items()):
            hostname = f"{name}.test"
            public = Path(site["root"]) / "public"
            socket = self.paths.runtime / "php" / site["php"] / "fpm.sock"
            access_log = self.paths.state / "logs" / "sites" / f"{name}.json"
            if site["secured"]:
                certificate = self.paths.data / "pki" / "sites" / name / "certificate.pem"
                private_key = self.paths.data / "pki" / "sites" / name / "private-key.pem"
                address = f"https://{hostname}"
            else:
                certificate = private_key = None
                address = f"http://{hostname}"
            lines.extend(
                [
                    f"{address} {{",
                    "\tbind 127.0.0.1",
                    f"\troot * {_quote(public)}",
                ]
            )
            if certificate is not None and private_key is not None:
                lines.append(f"\ttls {_quote(certificate)} {_quote(private_key)}")
            lines.extend(
                [
                    f"\tphp_fastcgi unix/{socket}",
                    "\tfile_server",
                    "\tlog {",
                    f"\t\toutput file {_quote(access_log)}",
                    "\t\tformat json",
                    "\t}",
                    "}",
                    "",
                ]
            )
        return "\n".join(lines)

    def validate(self, candidate: str) -> None:
        self.paths.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(
            prefix=".Caddyfile.candidate.", dir=self.paths.state
        )
        candidate_path = Path(name)
        try:
            with open(descriptor, "w", encoding="utf-8", closefd=True) as stream:
                stream.write(candidate)
                stream.flush()
            result = self.runner(
                ["caddy", "validate", "--config", str(candidate_path), "--adapter", "caddyfile"],
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
                raise CaddyError(f"Caddy rejected generated configuration: {detail}")
        finally:
            candidate_path.unlink(missing_ok=True)

    def write(self, candidate: str) -> None:
        atomic_write(self.path, candidate.encode())

    def reload(self) -> None:
        result = self.runner(
            [
                "caddy",
                "reload",
                "--config",
                str(self.path),
                "--adapter",
                "caddyfile",
                "--address",
                "127.0.0.1:20195",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise CaddyError(f"Caddy reload failed: {detail}")


def _quote(path: Path) -> str:
    return json.dumps(str(path), ensure_ascii=False)
