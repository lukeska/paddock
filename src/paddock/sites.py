from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from . import drivers, siteconfig
from .atomic import exclusive_lock
from .projects import ProjectError, select_node, select_php
from .node_runtime import NodeRegistry, normalize_major
from .runtimes import RuntimeRegistry
from .state import StateStore
from .web import WebProjector


SITE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class SiteError(ValueError):
    pass


@dataclass(frozen=True)
class Site:
    name: str
    root: Path
    php: str
    secured: bool
    node: str | None = None
    driver: str = drivers.DEFAULT
    # Relative to `root`; "." is the project root itself.
    document_root: str = "public"
    # The nginx fragment this project declares, if any, and the digest trust
    # was granted to. A path with no digest is declared but never reviewed.
    project_config: str | None = None
    project_config_trusted: str | None = None

    @classmethod
    def from_record(cls, record: dict) -> "Site":
        """Build a view from a stored record.

        Every caller used to construct this positionally and they had already
        drifted — securing a site returned one with its Node version dropped.
        One reader means a new field reaches every consumer at once.
        """
        return cls(
            name=record["name"],
            root=Path(record["root"]),
            php=record["php"],
            secured=record["secured"],
            node=record.get("node"),
            driver=record.get("type", drivers.DEFAULT),
            document_root=record.get("document_root", "public"),
            project_config=(record.get("nginx") or {}).get("path"),
            project_config_trusted=(record.get("nginx") or {}).get("sha256"),
        )

    @property
    def served(self) -> Path:
        """The absolute directory nginx serves for this site."""
        return drivers.document_path(self.root, self.document_root)


class SiteManager:
    def __init__(self, store: StateStore, projector: WebProjector):
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
        return [Site.from_record(record) for _, record in sorted(records.items())]

    def link(
        self,
        root: Path,
        name: str | None = None,
        php: str | None = None,
        node: str | None = None,
        *,
        driver: str | None = None,
        document_root: str | None = None,
        reload: bool = True,
    ) -> Site:
        canonical_root = root.expanduser().resolve(strict=True)
        if not canonical_root.is_dir():
            raise SiteError(f"project root is not a directory: {canonical_root}")
        site_name = normalize_site_name(name or canonical_root.name)
        version = php or select_php(canonical_root, self.store).version
        RuntimeRegistry(self.store).resolve(version)
        node_version = normalize_major(node) if node else None
        if node_version is None:
            try: node_version = select_node(canonical_root, self.store).version
            except ProjectError: pass
        if node_version is not None: NodeRegistry(self.store).resolve(node_version)

        with exclusive_lock(self.transaction_lock):
            registry = self.store.read("sites")
            sites = dict(registry["sites"])
            for existing_name, record in sites.items():
                if existing_name != site_name and Path(record["root"]) == canonical_root:
                    raise SiteError(
                        f"project is already linked as {existing_name}.test: {canonical_root}"
                    )
            previous = sites.get(site_name, {})
            secured = bool(previous.get("secured", False))
            selected, served = self._driver(
                canonical_root, driver, document_root, previous
            )
            record = {
                "name": site_name,
                "root": str(canonical_root),
                "php": version,
                "secured": secured,
                "type": selected.name,
                "document_root": served,
            }
            if node_version is not None: record["node"] = node_version
            if previous.get("reverb") is not None:
                record["reverb"] = previous["reverb"]
            if previous.get("queue") is not None:
                record["queue"] = previous["queue"]
            # Preserved rather than re-derived: only the project file knows
            # what is declared, and only `init` reads it.
            if previous.get("nginx") is not None:
                record["nginx"] = previous["nginx"]
            if previous.get("origin") == "parked" and Path(previous["root"]) == canonical_root:
                record.update({
                    "origin": "parked",
                    "parking_path": previous["parking_path"],
                })
            sites[site_name] = record
            candidate = self.projector.render(sites)
            self.projector.validate(candidate)
            self.store.write(
                "sites", {"schema_version": registry["schema_version"], "sites": sites}
            )
            self.projector.write(candidate)
            if reload:
                self.projector.reload()
        if previous.get("reverb") is not None:
            from .reverb import ReverbManager
            ReverbManager(self.store, self.projector.runner).reproject(site_name)
        if previous.get("queue") is not None:
            from .queue_worker import QueueWorkerManager
            QueueWorkerManager(self.store, self.projector.runner).reproject(site_name)
        return Site.from_record(record)

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
        return Site.from_record(record)

    def resolve_name(
        self, name: str | None = None, directory: Path | None = None
    ) -> str:
        """Name a site explicitly or by the directory the caller is in."""
        if name:
            return normalize_site_name(name)
        return self._name_for_directory(
            self.store.read("sites")["sites"], directory or Path.cwd()
        )

    def configurations(self) -> dict[str, siteconfig.SiteConfiguration]:
        """Resolve both fragments for every site, reading the registry once."""
        records = self.store.read("sites")["sites"]
        return {
            name: siteconfig.describe(self.store.paths, name, record)
            for name, record in sorted(records.items())
        }

    def configuration(self, name: str) -> siteconfig.SiteConfiguration:
        records = self.store.read("sites")["sites"]
        site_name = normalize_site_name(name)
        if site_name not in records:
            raise SiteError(f"site is not linked: {site_name}.test")
        return siteconfig.describe(self.store.paths, site_name, records[site_name])

    def declare_project_configuration(
        self, name: str, relative: str | None, *, reload: bool = True
    ) -> siteconfig.SiteConfiguration:
        """Record what a project file declares, without granting trust."""
        def mutate(record: dict) -> dict:
            return siteconfig.declare(record, relative)

        return self._commit(name, mutate, reload=reload)

    def trust_project_configuration(
        self, name: str, *, trusted: bool, reload: bool = True
    ) -> siteconfig.SiteConfiguration:
        """Grant or withdraw trust in a project's nginx fragment.

        Trust is the digest, not a flag, so an edit or a pull that changes the
        file withdraws it without anyone having to notice.
        """
        def mutate(record: dict) -> dict:
            declaration = record.get("nginx")
            if not declaration:
                raise SiteError(
                    f"{record['name']}.test declares no project nginx configuration"
                )
            path = Path(record["root"]) / declaration["path"]
            if not trusted:
                return {**record, "nginx": {"path": declaration["path"]}}
            current = siteconfig.digest(path)
            if current is None:
                raise SiteError(f"cannot read project nginx configuration: {path}")
            return {**record, "nginx": {"path": declaration["path"], "sha256": current}}

        return self._commit(name, mutate, reload=reload)

    def reproject(self, *, reload: bool = True) -> None:
        """Re-render from unchanged state.

        A user fragment lives outside the registry, so nothing about a site
        changes when one is written or edited — but the projection does, since
        an include is only emitted for a fragment that exists.
        """
        with exclusive_lock(self.transaction_lock):
            sites = self.store.read("sites")["sites"]
            candidate = self.projector.render(sites)
            self.projector.validate(candidate)
            self.projector.write(candidate)
            if reload:
                self.projector.reload()

    def _commit(self, name: str, mutate, *, reload: bool) -> siteconfig.SiteConfiguration:
        """Apply one record change through the projection transaction.

        The ordering is the same one `link` uses and for the same reason: the
        registry is written between validation and promotion, so a rejected
        configuration leaves both disk and state as they were.
        """
        site_name = normalize_site_name(name)
        with exclusive_lock(self.transaction_lock):
            registry = self.store.read("sites")
            sites = dict(registry["sites"])
            if site_name not in sites:
                raise SiteError(f"site is not linked: {site_name}.test")
            sites[site_name] = mutate(dict(sites[site_name]))
            candidate = self.projector.render(sites)
            self.projector.validate(candidate)
            self.store.write(
                "sites",
                {"schema_version": registry["schema_version"], "sites": sites},
            )
            self.projector.write(candidate)
            if reload:
                self.projector.reload()
            return siteconfig.describe(
                self.store.paths, site_name, sites[site_name]
            )

    def _driver(
        self,
        root: Path,
        requested: str | None,
        document_root: str | None,
        previous: dict,
    ) -> tuple[drivers.Driver, str]:
        """Decide what this project is and which directory is served.

        An explicit request wins, and choosing a new type recomputes the
        document root rather than keeping one that belonged to the old layout.
        Otherwise a previously recorded type is preserved, so `paddock init`
        does not silently revert a `--type` a user chose. Only a site with no
        recorded type is detected, which is its first link.
        """
        if requested is not None:
            selected = drivers.resolve(requested)
            override = document_root
        elif previous.get("type"):
            selected = drivers.resolve(previous["type"])
            override = document_root or previous.get("document_root")
        else:
            selected = drivers.detect(root)
            override = document_root
        relative = drivers.document_root(root, selected, override)
        served = drivers.document_path(root, relative)
        if not served.is_dir():
            raise SiteError(
                f"{selected.name} project has no document root at {served}; "
                "pass --root to name one, or --type to choose another project type"
            )
        return selected, relative

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
