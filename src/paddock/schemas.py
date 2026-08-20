from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


class SchemaError(ValueError):
    pass


def default_settings() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "default_php": None}


def default_runtimes() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "runtimes": {}}


def default_sites() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "sites": {}}


def default_services() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "services": {}}


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError(f"{label} must be an object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    unknown = set(value) - expected
    missing = expected - set(value)
    if missing:
        raise SchemaError(f"{label} is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise SchemaError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")


def _version(value: dict[str, Any], label: str) -> None:
    if value.get("schema_version") != SCHEMA_VERSION:
        raise SchemaError(
            f"{label} schema_version must be {SCHEMA_VERSION}; "
            f"got {value.get('schema_version')!r}"
        )


def validate_settings(raw: Any) -> dict[str, Any]:
    value = _object(raw, "settings")
    _exact_keys(value, {"schema_version", "default_php"}, "settings")
    _version(value, "settings")
    default = value["default_php"]
    if default is not None and (not isinstance(default, str) or not default):
        raise SchemaError("settings.default_php must be null or a non-empty string")
    return value


def validate_runtimes(raw: Any) -> dict[str, Any]:
    value = _object(raw, "runtime registry")
    _exact_keys(value, {"schema_version", "runtimes"}, "runtime registry")
    _version(value, "runtime registry")
    runtimes = _object(value["runtimes"], "runtimes")
    for version, record_raw in runtimes.items():
        if not isinstance(version, str) or not version:
            raise SchemaError("runtime versions must be non-empty strings")
        record = _object(record_raw, f"runtime {version}")
        _exact_keys(record, {"path", "version", "sha256"}, f"runtime {version}")
        if record["version"] != version:
            raise SchemaError(f"runtime {version} has a mismatched version field")
        for field in ("path", "sha256"):
            if not isinstance(record[field], str) or not record[field]:
                raise SchemaError(f"runtime {version}.{field} must be a non-empty string")
        if not Path(record["path"]).is_absolute():
            raise SchemaError(f"runtime {version}.path must be absolute")
    return value


def validate_sites(raw: Any) -> dict[str, Any]:
    value = _object(raw, "site registry")
    _exact_keys(value, {"schema_version", "sites"}, "site registry")
    _version(value, "site registry")
    sites = _object(value["sites"], "sites")
    for name, record_raw in sites.items():
        if not isinstance(name, str) or not name or name != name.lower():
            raise SchemaError("site names must be non-empty lowercase strings")
        record = _object(record_raw, f"site {name}")
        _exact_keys(record, {"name", "root", "php", "secured"}, f"site {name}")
        if record["name"] != name:
            raise SchemaError(f"site {name} has a mismatched name field")
        if not isinstance(record["root"], str) or not Path(record["root"]).is_absolute():
            raise SchemaError(f"site {name}.root must be an absolute path")
        if not isinstance(record["php"], str) or not record["php"]:
            raise SchemaError(f"site {name}.php must be a non-empty string")
        if not isinstance(record["secured"], bool):
            raise SchemaError(f"site {name}.secured must be a boolean")
    return value


def validate_services(raw: Any) -> dict[str, Any]:
    value = _object(raw, "service registry")
    _exact_keys(value, {"schema_version", "services"}, "service registry")
    _version(value, "service registry")
    services = _object(value["services"], "services")
    for name, record_raw in services.items():
        if not isinstance(name, str) or not name or name != name.lower():
            raise SchemaError("service names must be non-empty lowercase strings")
        record = _object(record_raw, f"service {name}")
        _exact_keys(record, {"name", "image", "port", "volume"}, f"service {name}")
        if record["name"] != name:
            raise SchemaError(f"service {name} has a mismatched name field")
        for field in ("image", "volume"):
            if not isinstance(record[field], str) or not record[field]:
                raise SchemaError(f"service {name}.{field} must be a non-empty string")
        # A bool is an int in Python, and True would validate as port 1.
        port = record["port"]
        if isinstance(port, bool) or not isinstance(port, int):
            raise SchemaError(f"service {name}.port must be an integer")
        if not 1 <= port <= 65535:
            raise SchemaError(f"service {name}.port must be between 1 and 65535")
    return value


VALIDATORS: dict[str, Callable[[Any], dict[str, Any]]] = {
    "settings": validate_settings,
    "runtimes": validate_runtimes,
    "sites": validate_sites,
    "services": validate_services,
}

DEFAULTS: dict[str, Callable[[], dict[str, Any]]] = {
    "settings": default_settings,
    "runtimes": default_runtimes,
    "sites": default_sites,
    "services": default_services,
}

