from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Callable

from .atomic import atomic_write, exclusive_lock
from .caddy import CaddyProjector
from .sites import Site, SiteError, SiteManager, normalize_site_name
from .state import StateStore


class TlsError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


class SecurityManager:
    def __init__(
        self,
        store: StateStore,
        projector: CaddyProjector,
        runner: Runner = subprocess.run,
    ):
        self.store = store
        self.projector = projector
        self.runner = runner
        self.lock = store.paths.state / "site-transaction.lock"

    def secure(
        self, name: str | None = None, directory: Path | None = None, *, reload: bool = True
    ) -> Site:
        with exclusive_lock(self.lock):
            registry = self.store.read("sites")
            sites = dict(registry["sites"])
            site_name = self._resolve_name(sites, name, directory)
            record = dict(sites[site_name])
            if record["secured"]:
                return Site(site_name, Path(record["root"]), record["php"], True)
            certificate_dir = self.store.paths.data / "pki" / "sites" / site_name
            certificate = certificate_dir / "certificate.pem"
            private_key = certificate_dir / "private-key.pem"
            certificate_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(
                prefix=f".{site_name}.", dir=certificate_dir
            ) as temporary_name:
                temporary = Path(temporary_name)
                candidate_certificate = temporary / "certificate.pem"
                candidate_key = temporary / "private-key.pem"
                result = self.runner(
                    [
                        "mkcert",
                        "-cert-file", str(candidate_certificate),
                        "-key-file", str(candidate_key),
                        f"{site_name}.test",
                        f"*.{site_name}.test",
                    ],
                    env={"CAROOT": str(self.store.paths.data / "pki")},
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if result.returncode != 0:
                    detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
                    raise TlsError(f"certificate issuance failed: {detail}")
                if not candidate_certificate.is_file() or not candidate_key.is_file():
                    raise TlsError("mkcert did not produce the expected certificate and key")
                certificate_before = certificate.read_bytes() if certificate.exists() else None
                key_before = private_key.read_bytes() if private_key.exists() else None
                try:
                    atomic_write(certificate, candidate_certificate.read_bytes())
                    atomic_write(private_key, candidate_key.read_bytes())
                    record["secured"] = True
                    sites[site_name] = record
                    candidate = self.projector.render(sites)
                    self.projector.validate(candidate)
                    self.store.write(
                        "sites",
                        {"schema_version": registry["schema_version"], "sites": sites},
                    )
                    self.projector.write(candidate)
                    if reload:
                        self.projector.reload()
                except Exception:
                    _restore(certificate, certificate_before)
                    _restore(private_key, key_before)
                    raise
            return Site(site_name, Path(record["root"]), record["php"], True)

    def unsecure(
        self, name: str | None = None, directory: Path | None = None, *, reload: bool = True
    ) -> Site:
        with exclusive_lock(self.lock):
            registry = self.store.read("sites")
            sites = dict(registry["sites"])
            site_name = self._resolve_name(sites, name, directory)
            record = dict(sites[site_name])
            if not record["secured"]:
                return Site(site_name, Path(record["root"]), record["php"], False)
            record["secured"] = False
            sites[site_name] = record
            candidate = self.projector.render(sites)
            self.projector.validate(candidate)
            self.store.write(
                "sites", {"schema_version": registry["schema_version"], "sites": sites}
            )
            self.projector.write(candidate)
            if reload:
                self.projector.reload()
            shutil.rmtree(self.store.paths.data / "pki" / "sites" / site_name, ignore_errors=True)
            return Site(site_name, Path(record["root"]), record["php"], False)

    @staticmethod
    def _resolve_name(
        sites: dict[str, dict], name: str | None, directory: Path | None
    ) -> str:
        site_name = (
            normalize_site_name(name)
            if name
            else SiteManager._name_for_directory(sites, directory or Path.cwd())
        )
        if site_name not in sites:
            raise SiteError(f"site is not linked: {site_name}.test")
        return site_name


def _restore(path: Path, value: bytes | None) -> None:
    if value is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write(path, value)
