"""`paddock.yml`: the committed description of what a project needs.

The point is that a freshly cloned repository becomes usable with one command.
`paddock init` reads this file and converges the machine towards it.

Two rules shape everything here. Applying must be **idempotent**, so running it
twice is indistinguishable from running it once. And it must never silently
undo something it did not declare: supporting services are shared between
projects by ADR 0010, so a project asking for PostgreSQL 17 on a machine
already running 16 is told about the difference rather than having its wish
imposed on everyone else's databases.

The schema is strict. An unknown key is a hard error, matching the state
records, because a typo in a committed file should fail loudly on the first
machine rather than quietly do nothing on all of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runtimes import normalize_minor
from .services import CATALOG
from .sites import normalize_site_name


PROJECT_FILE = "paddock.yml"

TOP_LEVEL = {"name", "php", "secure", "services"}
SERVICE_KEYS = {"version", "port"}

# Declared but not implemented. Named explicitly so the error says "not yet"
# rather than "unknown", which would read like a typo.
PLANNED = {"aliases", "env"}


class ProjectFileError(ValueError):
    pass


@dataclass(frozen=True)
class DeclaredService:
    name: str
    version: str | None = None
    port: int | None = None

    def image(self) -> str:
        """Resolve a version to the catalog's registry-qualified reference.

        Only the tag is substituted; the registry and repository stay
        Paddock's, so a project file cannot point the machine at an arbitrary
        image.
        """
        base = CATALOG[self.name].image.rsplit(":", 1)[0]
        return f"{base}:{self.version}" if self.version else CATALOG[self.name].image


@dataclass(frozen=True)
class ProjectFile:
    name: str | None = None
    php: str | None = None
    secure: bool = False
    services: tuple[DeclaredService, ...] = field(default_factory=tuple)


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProjectFileError(f"{label} must be a mapping")
    return value


def _reject_unknown(value: dict[str, Any], allowed: set[str], label: str) -> None:
    for key in value:
        if key in PLANNED:
            raise ProjectFileError(
                f"{label}: '{key}' is planned but not supported yet; remove it"
            )
        if key not in allowed:
            raise ProjectFileError(
                f"{label}: unknown key '{key}'; expected one of {', '.join(sorted(allowed))}"
            )


def parse(raw: Any) -> ProjectFile:
    """Validate a decoded document. Kept separate from reading, so the schema
    is testable without touching a filesystem or a YAML parser."""
    if raw is None:
        # An empty file is legitimate: link this directory, decide nothing else.
        return ProjectFile()
    value = _object(raw, PROJECT_FILE)
    _reject_unknown(value, TOP_LEVEL, PROJECT_FILE)

    name = value.get("name")
    if name is not None:
        if not isinstance(name, str):
            raise ProjectFileError("name must be a string")
        name = normalize_site_name(name)

    php = value.get("php")
    if php is not None:
        # A bare 8.5 in YAML decodes to a float and loses the trailing zero on
        # 8.10, so require the quoted form the documentation shows.
        if not isinstance(php, str):
            raise ProjectFileError(f'php must be a quoted string, e.g. php: "8.5"')
        php = normalize_minor(php)

    secure = value.get("secure", False)
    if not isinstance(secure, bool):
        raise ProjectFileError("secure must be true or false")

    declared = []
    services = value.get("services")
    if services is not None:
        for service_name, body in _object(services, "services").items():
            if service_name not in CATALOG:
                raise ProjectFileError(
                    f"unknown service '{service_name}'; "
                    f"supported: {', '.join(sorted(CATALOG))}"
                )
            body = {} if body is None else _object(body, f"services.{service_name}")
            _reject_unknown(body, SERVICE_KEYS, f"services.{service_name}")
            version = body.get("version")
            if version is not None and not isinstance(version, str):
                raise ProjectFileError(
                    f'services.{service_name}.version must be a quoted string'
                )
            port = body.get("port")
            if port is not None and (isinstance(port, bool) or not isinstance(port, int)):
                raise ProjectFileError(f"services.{service_name}.port must be an integer")
            declared.append(DeclaredService(service_name, version, port))

    return ProjectFile(name=name, php=php, secure=secure, services=tuple(declared))


def find(directory: Path) -> Path | None:
    candidate = directory / PROJECT_FILE
    return candidate if candidate.is_file() else None


def load(path: Path) -> ProjectFile:
    try:
        import yaml
    except ImportError as error:  # pragma: no cover - packaging guarantees it
        raise ProjectFileError(
            "PyYAML is required to read paddock.yml; install python-yaml"
        ) from error
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ProjectFileError(f"cannot read {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ProjectFileError(f"{path} is not valid YAML: {error}") from error
    return parse(raw)


@dataclass(frozen=True)
class Step:
    """One line of the report `paddock init` prints.

    `changed` distinguishes work done from state already correct, which is how
    a second run proves it was idempotent. `blocked` is neither: something the
    project asked for that Paddock declined to impose.
    """

    outcome: str          # "changed" | "unchanged" | "blocked"
    detail: str

    @property
    def marker(self) -> str:
        return {"changed": "+", "unchanged": "=", "blocked": "!"}[self.outcome]


class Reconciler:
    """Converge the machine towards a project file, and say what it did."""

    def __init__(self, store, sites, security, services, runner=None):
        self.store = store
        self.sites = sites
        self.security = security
        self.services = services

    def apply(self, root: Path, declared: ProjectFile, *, dry_run: bool = False) -> list[Step]:
        steps: list[Step] = []
        site_name = declared.name or normalize_site_name(root.name)
        existing = {site.name: site for site in self.sites.list()}
        current = existing.get(site_name)

        # A name already taken by a different directory is the one case that
        # cannot be resolved automatically without breaking someone else.
        if current is not None and current.root != root:
            steps.append(Step("blocked", f"{site_name}.test already serves {current.root}"))
            return steps

        if current is None:
            steps.append(Step("changed", f"link {root} as {site_name}.test"))
            if not dry_run:
                self.sites.link(root, site_name, declared.php)
                current = {site.name: site for site in self.sites.list()}[site_name]
        else:
            steps.append(Step("unchanged", f"{site_name}.test already linked"))
            if declared.php and current.php != declared.php:
                steps.append(
                    Step("changed", f"switch {site_name}.test to PHP {declared.php}")
                )
                if not dry_run:
                    self.sites.link(root, site_name, declared.php)
            elif declared.php:
                steps.append(Step("unchanged", f"already on PHP {declared.php}"))

        secured = current.secured if current and not dry_run else bool(current and current.secured)
        if declared.secure and not secured:
            steps.append(Step("changed", f"issue a certificate for {site_name}.test"))
            if not dry_run:
                self.security.secure(site_name, root)
        elif declared.secure:
            steps.append(Step("unchanged", f"{site_name}.test already served over HTTPS"))

        steps.extend(self._services(declared, dry_run=dry_run))
        return steps

    def _services(self, declared: ProjectFile, *, dry_run: bool) -> list[Step]:
        if not declared.services:
            return []
        steps: list[Step] = []
        configured = {service.name: service for service in self.services.list()}
        for wanted in declared.services:
            current = configured.get(wanted.name)
            image, port = wanted.image(), wanted.port or CATALOG[wanted.name].port
            if current is None:
                steps.append(Step("changed", f"start {wanted.name} on 127.0.0.1:{port}"))
                if not dry_run:
                    self.services.configure(wanted.name, image, port)
                    self.services.control("start", wanted.name)
                continue
            # Services are shared between projects. Changing one because this
            # project asked would silently repoint every other project's
            # database, so the difference is reported and left alone.
            if current.image != image or current.port != port:
                steps.append(Step(
                    "blocked",
                    f"{wanted.name} is already running {current.image} on "
                    f"{current.address}; this project asks for {image} on port {port}",
                ))
                continue
            if self.services.state_of(current) != "active":
                steps.append(Step("changed", f"start {wanted.name}"))
                if not dry_run:
                    self.services.control("start", wanted.name)
            else:
                steps.append(Step("unchanged", f"{wanted.name} already running"))
        return steps
