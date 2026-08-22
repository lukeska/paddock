"""One machine-readable snapshot of everything Paddock knows.

This is the interface the Omarchy plugin consumes, and the reason it exists is
cost: a bar widget is instantiated once per monitor and refreshes on a timer,
so screen-scraping `status`, `php list`, `services` and `doctor` would fork
four processes per screen per tick. One command, one JSON document, two
subprocess calls.

`systemctl is-active` accepts many units and prints one line each in the order
given, so the whole unit picture costs a single fork per manager. Everything
else already lives in state.

Nothing here mutates. Any failure degrades the affected field rather than
raising, because a status display that crashes is worse than one reporting
that it does not know.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
from typing import Any, Callable

from .caddy import CaddyProjector
from .runtimes import RuntimeRegistry
from .services import ENGINE, ServiceManager
from .sites import SiteManager
from .state import StateError, StateStore


SCHEMA_VERSION = 1

# Units the stack always owns, in start order. Per-runtime PHP units are
# appended by `units_for`, because which minors exist is user state.
CORE_UNITS = (
    "paddock.target",
    "paddock-dns.service",
    "paddock-dns-route.service",
    "paddock-caddy.service",
)

# Paddock-built runtimes unpack to `<name>-<major>.<minor>.<patch>-<digest>`.
# The registry stores only the minor, so the release is recovered from the
# directory name and is None whenever the layout does not match. Callers must
# treat it as decoration; the minor is the identity.
RELEASE_DIRECTORY = re.compile(r"^php-(?P<release>[0-9]+\.[0-9]+\.[0-9]+)-[0-9a-f]+$")

Runner = Callable[..., subprocess.CompletedProcess[str]]


def php_unit(minor: str) -> str:
    return f"paddock-php@{minor}.service"


def units_for(store: StateStore) -> list[str]:
    """Every system unit the current installation should be running.

    `status` used to name three units and omit PHP-FPM entirely, so it reported
    a healthy stack while both masters were dead. The list is derived here once
    and shared, so the two commands cannot disagree again.
    """
    try:
        minors = [runtime.version for runtime in RuntimeRegistry(store).list()]
    except (StateError, ValueError):
        minors = []
    return [*CORE_UNITS, *(php_unit(minor) for minor in minors)]


def active_states(units: list[str], runner: Runner, *, user: bool = False) -> dict[str, str]:
    """Ask systemd about many units at once.

    `is-active` exits non-zero when any unit is inactive, which is not an
    error here, and prints one line per unit in the order given. A short reply
    (systemctl missing, or killed) leaves the remaining units `unknown` rather
    than shifting every answer onto the wrong unit.
    """
    if not units:
        return {}
    command = ["systemctl", *(["--user"] if user else []), "is-active", *units]
    try:
        result = runner(command, text=True, capture_output=True, check=False)
    except OSError:
        return {unit: "unknown" for unit in units}
    lines = (result.stdout or "").splitlines()
    return {
        unit: (lines[index].strip() if index < len(lines) and lines[index].strip() else "unknown")
        for index, unit in enumerate(units)
    }


def _release_for(path: Path) -> str | None:
    for parent in path.parents:
        matched = RELEASE_DIRECTORY.match(parent.name)
        if matched:
            return matched.group("release")
    return None


def _php(store: StateStore, states: dict[str, str]) -> dict[str, Any]:
    try:
        runtimes = RuntimeRegistry(store).list()
    except (StateError, ValueError):
        runtimes = []
    try:
        default = store.read("settings")["default_php"]
    except StateError:
        default = None
    return {
        "default": default,
        "runtimes": [
            {
                "minor": runtime.version,
                "release": _release_for(runtime.path),
                "unit": php_unit(runtime.version),
                "state": states.get(php_unit(runtime.version), "unknown"),
                "path": str(runtime.path),
            }
            for runtime in runtimes
        ],
    }


def _sites(store: StateStore) -> list[dict[str, Any]]:
    try:
        sites = SiteManager(store, CaddyProjector(store.paths)).list()
    except (StateError, ValueError):
        return []
    return [
        {
            "name": site.name,
            "host": f"{site.name}.test",
            "url": f"{'https' if site.secured else 'http'}://{site.name}.test",
            "php": site.php,
            "secured": site.secured,
            "root": str(site.root),
        }
        for site in sites
    ]


def health(
    units: list[dict[str, Any]], services: list[dict[str, Any]], linger: bool
) -> str:
    """Roll the whole picture into one word for the bar.

    `unknown` is deliberately absent: it means the report could not be produced
    at all, which only the caller can observe.
    """
    states = {unit["name"]: unit["state"] for unit in units}
    if states.get("paddock.target") != "active":
        return "down"
    if any(not unit["ok"] for unit in units):
        return "degraded"
    if any(service["state"] != "active" for service in services):
        return "degraded"
    # A service that is up now but stops at logout is not healthy, it is
    # temporarily lucky.
    if services and not linger:
        return "degraded"
    return "ok"


def build(store: StateStore, runner: Runner = subprocess.run) -> dict[str, Any]:
    names = units_for(store)
    states = active_states(names, runner)
    units = [
        {"name": name, "state": states.get(name, "unknown"),
         "ok": states.get(name) == "active"}
        for name in names
    ]

    manager = ServiceManager(store, runner)
    try:
        configured = manager.list()
    except (StateError, ValueError):
        configured = []
    service_states = active_states(
        [service.unit for service in configured], runner, user=True
    )
    services = [
        {
            "name": service.name,
            "state": service_states.get(service.unit, "unknown"),
            "address": service.address,
            "image": service.image,
            "unit": service.unit,
        }
        for service in configured
    ]
    # Only meaningful once a service exists, and the check forks, so skip it.
    linger = manager.lingering() if configured else False

    return {
        "schema_version": SCHEMA_VERSION,
        "health": health(units, services, linger),
        "units": units,
        "php": _php(store, states),
        "services": services,
        "engine": ENGINE,
        "linger": linger,
        "sites": _sites(store),
    }
