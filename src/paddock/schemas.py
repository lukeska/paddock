from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


class SchemaError(ValueError):
    pass


def default_settings() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "default_php": None,
        "service_labels": {},
        "initial_php_setup_complete": False,
        "default_node": None,
        "initial_node_setup_complete": False,
    }


def default_runtimes() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "runtimes": {}}


def default_node_runtimes() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "runtimes": {}}


def default_sites() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "sites": {}}


def default_services() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "services": {}}


def default_parking() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "paths": []}


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
    unknown = set(value) - {
        "schema_version", "default_php", "service_labels",
        "initial_php_setup_complete",
        "default_node", "initial_node_setup_complete",
    }
    missing = {"schema_version", "default_php"} - set(value)
    if missing:
        raise SchemaError(f"settings is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise SchemaError(f"settings has unknown fields: {', '.join(sorted(unknown))}")
    _version(value, "settings")
    default = value["default_php"]
    if default is not None and (not isinstance(default, str) or not default):
        raise SchemaError("settings.default_php must be null or a non-empty string")
    labels = _object(value.get("service_labels", {}), "settings.service_labels")
    initial_php_setup_complete = value.get("initial_php_setup_complete", False)
    default_node = value.get("default_node")
    if default_node is not None and (not isinstance(default_node, str) or not default_node.isdigit()):
        raise SchemaError("settings.default_node must be null or a Node major version")
    initial_node_setup_complete = value.get("initial_node_setup_complete", False)
    if not isinstance(initial_node_setup_complete, bool):
        raise SchemaError("settings.initial_node_setup_complete must be a boolean")
    if not isinstance(initial_php_setup_complete, bool):
        raise SchemaError("settings.initial_php_setup_complete must be a boolean")
    for name, label in labels.items():
        if not isinstance(name, str) or not name:
            raise SchemaError("settings.service_labels keys must be non-empty strings")
        if not isinstance(label, str) or not label or len(label) > 80:
            raise SchemaError(
                f"settings.service_labels.{name} must be a string between 1 and 80 characters"
            )
        if any(ord(character) < 32 for character in label):
            raise SchemaError(f"settings.service_labels.{name} contains control characters")
    return {
        **value,
        "service_labels": labels,
        "initial_php_setup_complete": initial_php_setup_complete,
        "default_node": default_node,
        "initial_node_setup_complete": initial_node_setup_complete,
    }


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
    reverb_ports: set[int] = set()
    for name, record_raw in sites.items():
        if not isinstance(name, str) or not name or name != name.lower():
            raise SchemaError("site names must be non-empty lowercase strings")
        record = _object(record_raw, f"site {name}")
        required = {"name", "root", "php", "secured"}
        allowed = required | {"origin", "parking_path", "node", "reverb", "queue"}
        missing = required - set(record)
        unknown = set(record) - allowed
        if missing:
            raise SchemaError(f"site {name} is missing: {', '.join(sorted(missing))}")
        if unknown:
            raise SchemaError(f"site {name} has unknown fields: {', '.join(sorted(unknown))}")
        if record["name"] != name:
            raise SchemaError(f"site {name} has a mismatched name field")
        if not isinstance(record["root"], str) or not Path(record["root"]).is_absolute():
            raise SchemaError(f"site {name}.root must be an absolute path")
        if not isinstance(record["php"], str) or not record["php"]:
            raise SchemaError(f"site {name}.php must be a non-empty string")
        if record.get("node") is not None and (
            not isinstance(record["node"], str) or not record["node"].isdigit()
        ):
            raise SchemaError(f"site {name}.node must be a Node major version")
        reverb = record.get("reverb")
        if reverb is not None:
            if not isinstance(reverb, dict) or set(reverb) != {"port"}:
                raise SchemaError(f"site {name}.reverb must contain only port")
            port = reverb["port"]
            if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
                raise SchemaError(f"site {name}.reverb.port must be an unprivileged port")
            if port in reverb_ports:
                raise SchemaError("site Reverb ports must be unique")
            reverb_ports.add(port)
        queue = record.get("queue")
        if queue is not None and (
            not isinstance(queue, dict)
            or queue != {"configured": True}
        ):
            raise SchemaError(f"site {name}.queue must be a configured worker record")
        if not isinstance(record["secured"], bool):
            raise SchemaError(f"site {name}.secured must be a boolean")
        origin = record.get("origin", "linked")
        if origin not in {"linked", "parked"}:
            raise SchemaError(f"site {name}.origin must be linked or parked")
        parking_path = record.get("parking_path")
        if origin == "parked":
            if not isinstance(parking_path, str) or not Path(parking_path).is_absolute():
                raise SchemaError(f"site {name}.parking_path must be absolute when parked")
        elif parking_path is not None:
            raise SchemaError(f"site {name}.parking_path is only valid for parked sites")
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


def validate_parking(raw: Any) -> dict[str, Any]:
    value = _object(raw, "parking registry")
    _exact_keys(value, {"schema_version", "paths"}, "parking registry")
    _version(value, "parking registry")
    paths = value["paths"]
    if not isinstance(paths, list):
        raise SchemaError("parking paths must be an array")
    seen: set[str] = set()
    for index, path in enumerate(paths):
        if not isinstance(path, str) or not path:
            raise SchemaError(f"parking path {index} must be a non-empty string")
        if not Path(path).is_absolute():
            raise SchemaError(f"parking path {index} must be absolute")
        if path in seen:
            raise SchemaError(f"parking path is duplicated: {path}")
        seen.add(path)
    return value


VALIDATORS: dict[str, Callable[[Any], dict[str, Any]]] = {
    "settings": validate_settings,
    "runtimes": validate_runtimes,
    "node_runtimes": validate_runtimes,
    "sites": validate_sites,
    "services": validate_services,
    "parking": validate_parking,
}

DEFAULTS: dict[str, Callable[[], dict[str, Any]]] = {
    "settings": default_settings,
    "runtimes": default_runtimes,
    "node_runtimes": default_node_runtimes,
    "sites": default_sites,
    "services": default_services,
    "parking": default_parking,
}
