from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Callable

from .caddy import CaddyProjector
from .report import active_states, units_for
from .runtimes import RuntimeRegistry
from .services import ENGINE, ENGINE_HINT, ServiceManager
from .sites import SiteManager
from .state import StateError, StateStore


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


Runner = Callable[..., subprocess.CompletedProcess[str]]


def doctor(store: StateStore, runner: Runner = subprocess.run) -> list[Check]:
    checks = [
        Check(command, shutil.which(command) is not None, shutil.which(command) or "not found")
        for command in ("caddy", "dnsmasq", "mkcert")
    ]
    for record in ("settings", "runtimes", "sites", "services"):
        try:
            store.read(record)
            checks.append(Check(f"state:{record}", True, str(store.path_for(record))))
        except StateError as error:
            checks.append(Check(f"state:{record}", False, str(error)))
    try:
        runtimes = RuntimeRegistry(store).list()
    except (StateError, ValueError) as error:
        checks.append(Check("php:registry", False, str(error)))
        runtimes = []
    for runtime in runtimes:
        executable = runtime.path.is_file() and runtime.path.stat().st_mode & 0o111 != 0
        checks.append(Check(f"php:{runtime.version}", executable, str(runtime.path)))
    try:
        sites = SiteManager(store, CaddyProjector(store.paths, runner)).list()
    except (StateError, ValueError):
        sites = []
    for site in sites:
        public = site.root / "public"
        checks.append(Check(f"site:{site.name}", public.is_dir(), str(public)))
    # Only report on the container engine once a service actually wants it,
    # so a user who never configures one sees no failure for a missing podman.
    manager = ServiceManager(store, runner)
    try:
        configured = manager.list()
    except StateError as error:
        configured = []
        checks.append(Check("services", False, str(error)))
    if configured:
        engine = shutil.which(ENGINE)
        checks.append(Check(ENGINE, engine is not None, engine or f"not found: {ENGINE_HINT}"))
        # Without lingering the user manager stops at logout, so a service
        # that looks healthy now would not return after a reboot.
        lingering = manager.lingering()
        checks.append(
            Check(
                "services:linger",
                lingering,
                "enabled" if lingering else
                "disabled; services stop at logout. Re-run paddock setup",
            )
        )
    for service in configured:
        state = manager.state_of(service)
        checks.append(
            Check(
                f"service:{service.name}",
                state == "active",
                f"{state} on {service.address}" if state != "active" else service.address,
            )
        )
        unit = manager.unit_path(service.name)
        checks.append(Check(f"service:{service.name}:unit", unit.is_file(), str(unit)))
    caddy = CaddyProjector(store.paths, runner)
    if caddy.path.exists():
        try:
            caddy.validate(caddy.path.read_text(encoding="utf-8"))
            checks.append(Check("caddy:config", True, str(caddy.path)))
        except (OSError, RuntimeError) as error:
            checks.append(Check("caddy:config", False, str(error)))
    else:
        checks.append(Check("caddy:config", False, f"not generated: {caddy.path}"))
    return checks


def service_status(store: StateStore, runner: Runner = subprocess.run) -> list[Check]:
    """Report every unit the installation owns.

    This used to name three units and omit PHP-FPM entirely, so both masters
    could be dead while `paddock status` printed `active` for everything. The
    list now comes from `report.units_for`, shared with `paddock report`, and
    one batched `systemctl` call answers for all of them.
    """
    units = units_for(store)
    states = active_states(units, runner)
    return [
        Check(unit, states.get(unit) == "active", states.get(unit, "unknown"))
        for unit in units
    ]
