"""Parked folders whose immediate children become `.test` site candidates."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import subprocess
from typing import Callable

from . import drivers
from .atomic import atomic_write
from .runtimes import RuntimeRegistry
from .sites import normalize_site_name
from .state import StateStore
from .web import WebProjector


class ParkingError(ValueError):
    pass


def _systemd_path(value: Path) -> str:
    """Escape a path for an unquoted systemd unit-file assignment."""
    safe = frozenset(b"/._-")
    return "".join(
        chr(byte)
        if byte in safe or 48 <= byte <= 57 or 65 <= byte <= 90 or 97 <= byte <= 122
        else f"\\x{byte:02x}"
        for byte in str(value).encode("utf-8")
    )


@dataclass(frozen=True)
class ParkedSite:
    name: str
    root: Path
    parking_path: Path

    @property
    def host(self) -> str:
        return f"{self.name}.test"


@dataclass(frozen=True)
class ParkingConflict:
    name: str
    roots: tuple[Path, ...]
    reason: str


@dataclass(frozen=True)
class ParkingDiscovery:
    paths: tuple[Path, ...]
    sites: tuple[ParkedSite, ...]
    conflicts: tuple[ParkingConflict, ...]


class ParkingManager:
    def __init__(
        self,
        store: StateStore,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ):
        self.store = store
        self.runner = runner

    @property
    def unit_directory(self) -> Path:
        return self.store.paths.config.parent / "systemd/user"

    @property
    def reconcile_unit(self) -> Path:
        return self.unit_directory / "paddock-parking-reconcile.service"

    def ensure_default(self, home: Path) -> Path:
        """Create and register Paddock's equivalent of Herd's `~/Herd`."""
        home = home.expanduser()
        home.mkdir(parents=True, exist_ok=True)
        home = home.resolve(strict=True)
        default = home / "Paddock"
        default.mkdir(parents=True, exist_ok=True)
        paths = self.list()
        if default not in paths:
            self._write([*paths, default])
        return default

    def list(self) -> list[Path]:
        return [Path(value) for value in self.store.read("parking")["paths"]]

    def add(self, path: Path) -> Path:
        canonical = path.expanduser().resolve(strict=True)
        if not canonical.is_dir():
            raise ParkingError(f"parking path is not a directory: {canonical}")
        paths = self.list()
        if canonical not in paths:
            self._write([*paths, canonical])
        return canonical

    def remove(self, path: Path) -> Path:
        canonical = path.expanduser().resolve(strict=False)
        paths = self.list()
        if canonical not in paths:
            raise ParkingError(f"path is not parked: {canonical}")
        self._write([candidate for candidate in paths if candidate != canonical])
        return canonical

    def discover(self) -> ParkingDiscovery:
        paths = tuple(self.list())
        explicit = {
            name: record
            for name, record in self.store.read("sites")["sites"].items()
            if record.get("origin", "linked") == "linked"
        }
        candidates: dict[str, list[ParkedSite]] = {}
        conflicts: list[ParkingConflict] = []
        for parking_path in paths:
            if not parking_path.is_dir():
                conflicts.append(ParkingConflict(
                    "", (parking_path,), "parking path does not exist"
                ))
                continue
            for child in sorted(parking_path.iterdir(), key=lambda item: item.name.casefold()):
                if not child.is_dir() or child.name.startswith("."):
                    continue
                try:
                    name = normalize_site_name(child.name)
                except ValueError as error:
                    conflicts.append(ParkingConflict(
                        child.name, (child,), f"invalid site name: {error}"
                    ))
                    continue
                candidates.setdefault(name, []).append(
                    ParkedSite(name, child.resolve(), parking_path)
                )

        discovered: list[ParkedSite] = []
        for name, matches in sorted(candidates.items()):
            roots = tuple(item.root for item in matches)
            if name in explicit:
                if Path(explicit[name]["root"]) not in roots:
                    conflicts.append(ParkingConflict(
                        name, roots, "explicit link takes precedence"
                    ))
                continue
            if len(matches) > 1:
                conflicts.append(ParkingConflict(
                    name, roots, "duplicate site name across parked paths"
                ))
                continue
            discovered.append(matches[0])
        return ParkingDiscovery(paths, tuple(discovered), tuple(conflicts))

    def reconcile(
        self, projector: WebProjector, *, reload: bool = True
    ) -> ParkingDiscovery:
        """Materialize the current child folders without touching explicit links."""
        discovery = self.discover()
        registry = self.store.read("sites")
        explicit = {
            name: record for name, record in registry["sites"].items()
            if record.get("origin", "linked") == "linked"
        }
        previous = {
            name: record for name, record in registry["sites"].items()
            if record.get("origin") == "parked"
        }
        settings = self.store.read("settings")
        default_php = settings["default_php"]
        default_node = settings.get("default_node")
        if default_php is None:
            runtimes = RuntimeRegistry(self.store).list()
            default_php = runtimes[-1].version if runtimes else None
        conflicts = list(discovery.conflicts)
        parked: dict[str, dict] = {}
        materialized: list[ParkedSite] = []
        for site in discovery.sites:
            old = previous.get(site.name)
            if old is not None and Path(old["root"]) == site.root:
                php = old["php"]
                node = old.get("node", default_node)
                secured = old["secured"]
            else:
                if default_php is None:
                    conflicts.append(ParkingConflict(
                        site.name, (site.root,),
                        "no default or installed PHP version is available",
                    ))
                    continue
                php = default_php
                node = default_node
                secured = False
            # A parked folder gets the same detection an explicit link
            # does, or a WordPress folder would be served as Laravel. This
            # runs on a filesystem event, so resolution must not raise for a
            # folder that is mid-clone: `document_root` always answers,
            # falling back to the driver's last candidate.
            if old is not None and old.get("type"):
                driver = drivers.resolve(old["type"])
                relative = drivers.document_root(
                    site.root, driver, old.get("document_root")
                )
            else:
                driver = drivers.detect(site.root)
                relative = drivers.document_root(site.root, driver)
            parked[site.name] = {
                "name": site.name,
                "root": str(site.root),
                "php": php,
                "secured": secured,
                "type": driver.name,
                "document_root": relative,
                "origin": "parked",
                "parking_path": str(site.parking_path),
            }
            if node is not None:
                parked[site.name]["node"] = node
            if old is not None and old.get("reverb") is not None:
                parked[site.name]["reverb"] = old["reverb"]
            if old is not None and old.get("queue") is not None:
                parked[site.name]["queue"] = old["queue"]
            materialized.append(site)
        sites = {**explicit, **parked}
        if sites != registry["sites"]:
            candidate = projector.render(sites)
            projector.validate(candidate)
            self.store.write("sites", {
                "schema_version": registry["schema_version"], "sites": sites,
            })
            projector.write(candidate)
            if reload:
                projector.reload()
        return ParkingDiscovery(
            discovery.paths, tuple(materialized), tuple(conflicts)
        )

    def _write(self, paths: list[Path]) -> None:
        self.store.write("parking", {
            "schema_version": 1,
            "paths": [str(path) for path in sorted(paths, key=lambda item: str(item).casefold())],
        })

    def sync_watchers(self) -> tuple[Path, ...]:
        """Project one user path unit per parked folder and activate it."""
        paths = self.list()
        service = (
            "[Unit]\nDescription=Reconcile Paddock parked sites\n\n"
            "[Service]\nType=oneshot\nExecStart=/usr/bin/paddock park --refresh\n"
        )
        atomic_write(self.reconcile_unit, service.encode(), mode=0o644, parent_mode=None)
        desired: dict[str, Path] = {}
        for path in paths:
            digest = hashlib.sha256(str(path).encode()).hexdigest()[:12]
            unit = f"paddock-parking-{digest}.path"
            unit_path = self.unit_directory / unit
            rendered = (
                "[Unit]\nDescription=Watch Paddock parking folder\n\n"
                "[Path]\n"
                f"PathChanged={_systemd_path(path)}\n"
                "Unit=paddock-parking-reconcile.service\n\n"
                "[Install]\nWantedBy=default.target\n"
            )
            atomic_write(unit_path, rendered.encode(), mode=0o644, parent_mode=None)
            desired[unit] = unit_path

        existing = {
            path.name: path for path in self.unit_directory.glob("paddock-parking-*.path")
        }
        stale = sorted(set(existing) - set(desired))
        for unit in stale:
            self.runner(
                ["systemctl", "--user", "disable", "--now", unit],
                text=True, capture_output=True, check=False,
            )
            existing[unit].unlink(missing_ok=True)
        self.runner(
            ["systemctl", "--user", "daemon-reload"],
            text=True, capture_output=True, check=False,
        )
        for unit in sorted(desired):
            result = self.runner(
                ["systemctl", "--user", "enable", "--now", unit],
                text=True, capture_output=True, check=False,
            )
            if result.returncode:
                detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
                raise ParkingError(f"cannot enable parking watcher {unit}: {detail}")
        return tuple(desired[unit] for unit in sorted(desired))
