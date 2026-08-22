from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .atomic import exclusive_lock
from .caddy import CaddyProjector
from .projects import select_php
from .runtimes import RuntimeRegistry
from .state import StateStore


SITE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class SiteError(ValueError):
    pass


@dataclass(frozen=True)
class Site:
    name: str
    root: Path
    php: str
    secured: bool


class SiteManager:
    def __init__(self, store: StateStore, projector: CaddyProjector):
        self.store = store
        self.projector = projector
        self.transaction_lock = store.paths.state / "site-transaction.lock"

    def list(self) -> list[Site]:
        """Every linked site, sorted by name.

        Consumers previously each read `sites.json` themselves, so the site set
        had no single owner and nothing could present it to a user. Ordering is
        explicit rather than relying on the file happening to be written with
        sorted keys.
        """
        records = self.store.read("sites")["sites"]
        return [
            Site(
                name=record["name"],
                root=Path(record["root"]),
                php=record["php"],
                secured=record["secured"],
            )
            for _, record in sorted(records.items())
        ]

    def link(
        self,
        root: Path,
        name: str | None = None,
        php: str | None = None,
        *,
        reload: bool = True,
    ) -> Site:
        canonical_root = root.expanduser().resolve(strict=True)
        if not canonical_root.is_dir():
            raise SiteError(f"project root is not a directory: {canonical_root}")
        public = canonical_root / "public"
        if not public.is_dir():
            raise SiteError(f"project has no public directory: {public}")
        site_name = normalize_site_name(name or canonical_root.name)
        version = php or select_php(canonical_root, self.store).version
        RuntimeRegistry(self.store).resolve(version)

        with exclusive_lock(self.transaction_lock):
            registry = self.store.read("sites")
            sites = dict(registry["sites"])
            for existing_name, record in sites.items():
                if existing_name != site_name and Path(record["root"]) == canonical_root:
                    raise SiteError(
                        f"project is already linked as {existing_name}.test: {canonical_root}"
                    )
            record = {
                "name": site_name,
                "root": str(canonical_root),
                "php": version,
                "secured": False,
            }
            sites[site_name] = record
            candidate = self.projector.render(sites)
            self.projector.validate(candidate)
            self.store.write(
                "sites", {"schema_version": registry["schema_version"], "sites": sites}
            )
            self.projector.write(candidate)
            if reload:
                self.projector.reload()
        return Site(site_name, canonical_root, version, False)

    def unlink(
        self, name: str | None = None, directory: Path | None = None, *, reload: bool = True
    ) -> Site:
        with exclusive_lock(self.transaction_lock):
            registry = self.store.read("sites")
            sites = dict(registry["sites"])
            site_name = normalize_site_name(name) if name else self._name_for_directory(
                sites, directory or Path.cwd()
            )
            try:
                record = sites.pop(site_name)
            except KeyError as error:
                raise SiteError(f"site is not linked: {site_name}.test") from error
            candidate = self.projector.render(sites)
            self.projector.validate(candidate)
            self.store.write(
                "sites", {"schema_version": registry["schema_version"], "sites": sites}
            )
            self.projector.write(candidate)
            if reload:
                self.projector.reload()
        return Site(site_name, Path(record["root"]), record["php"], record["secured"])

    @staticmethod
    def _name_for_directory(sites: dict[str, dict], directory: Path) -> str:
        current = directory.expanduser().resolve(strict=True)
        candidates: list[tuple[int, str]] = []
        for name, record in sites.items():
            try:
                relative = current.relative_to(Path(record["root"]).resolve(strict=True))
            except (FileNotFoundError, ValueError):
                continue
            candidates.append((len(relative.parts), name))
        if not candidates:
            raise SiteError(f"current directory is not inside a linked site: {current}")
        return min(candidates)[1]


def normalize_site_name(name: str) -> str:
    value = name.removesuffix(".test").lower()
    if not SITE_NAME.fullmatch(value):
        raise SiteError(
            "site name must be a DNS label containing only lowercase letters, "
            "numbers, and interior hyphens"
        )
    return value
