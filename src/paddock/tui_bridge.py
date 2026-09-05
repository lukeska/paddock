"""Versioned NDJSON boundary used by the Paddock terminal UI.

The TUI is deliberately a small Go client.  All knowledge of Paddock state,
systemd units, and worker reconciliation stays in the Python application
controller so the GTK, CLI, and terminal clients cannot drift apart.
"""

from __future__ import annotations

from dataclasses import asdict
import json
import sys
import traceback
from typing import IO, Any

from .application import PaddockController
from .paths import Paths
from .state import StateStore
from .ui.theme import ThemeError, load_palette


# 2 added the project type, document root, and per-site nginx fragment
# fields to the site payload, plus site.set_configuration_trusted,
# site.ensure_configuration and web.reload. The bridge and the Go client ship in one package, so both
# sides check for equality and move together.
PROTOCOL_VERSION = 2
WORKERS = {"queue", "reverb"}


class RequestError(ValueError):
    def __init__(self, code: str, message: str, request_id: int | None = None):
        super().__init__(message)
        self.code = code
        self.request_id = request_id


def _required_string(params: dict[str, object], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value.strip():
        raise RequestError("invalid_params", f"{name} must be a non-empty string")
    return value


def _required_bool(params: dict[str, object], name: str) -> bool:
    value = params.get(name)
    if not isinstance(value, bool):
        raise RequestError("invalid_params", f"{name} must be a boolean")
    return value


def _worker(params: dict[str, object]) -> str:
    worker = _required_string(params, "worker")
    if worker not in WORKERS:
        raise RequestError("invalid_params", "worker must be queue or reverb")
    return worker


def _only(params: dict[str, object], allowed: set[str]) -> None:
    unexpected = sorted(set(params) - allowed)
    if unexpected:
        raise RequestError(
            "invalid_params", f"unexpected parameter: {unexpected[0]}"
        )


def _theme(palette_path: Path | None) -> dict[str, object] | None:
    if palette_path is None:
        return None
    try:
        return asdict(load_palette(palette_path))
    except ThemeError:
        # Theme application replaces this directory. A missing or momentarily
        # incomplete palette must not hide Paddock's operational data.
        return None


def dispatch(
    controller: PaddockController,
    method: str,
    params: dict[str, object],
    palette_path: Path | None = None,
) -> object:
    """Dispatch one validated bridge method to the application controller."""
    if method == "snapshot.get":
        _only(params, set())
        return {
            "protocol_version": PROTOCOL_VERSION,
            "dashboard": asdict(controller.dashboard_snapshot()),
            "services": asdict(controller.service_instances_snapshot()),
            "sites": asdict(controller.linked_sites_snapshot()),
            "theme": _theme(palette_path),
        }

    if method == "worker.set_active":
        _only(params, {"site", "worker", "active"})
        site = _required_string(params, "site")
        worker = _worker(params)
        active = _required_bool(params, "active")
        operation = getattr(controller, f"set_{worker}_active")
        return asdict(operation(site, active))

    if method == "dashboard.set_active":
        _only(params, {"active"})
        active = _required_bool(params, "active")
        return asdict(controller.set_dashboard_active(active))

    if method == "worker.set_autostart":
        _only(params, {"site", "worker", "enabled"})
        site = _required_string(params, "site")
        worker = _worker(params)
        enabled = _required_bool(params, "enabled")
        operation = getattr(controller, f"set_{worker}_autostart")
        return asdict(operation(site, enabled))

    if method in {"site.set_php", "site.set_node"}:
        _only(params, {"site", "version"})
        site = _required_string(params, "site")
        version = _required_string(params, "version")
        operation = getattr(controller, f"set_linked_site_{method.removeprefix('site.set_')}")
        return asdict(operation(site, version))

    if method == "site.set_secured":
        _only(params, {"site", "secured"})
        site = _required_string(params, "site")
        secured = _required_bool(params, "secured")
        return asdict(controller.set_linked_site_secured(site, secured))

    if method == "site.set_configuration_trusted":
        _only(params, {"site", "trusted"})
        site = _required_string(params, "site")
        trusted = _required_bool(params, "trusted")
        return asdict(controller.set_site_configuration_trusted(site, trusted))

    if method == "site.ensure_configuration":
        _only(params, {"site"})
        site = _required_string(params, "site")
        return asdict(controller.ensure_site_configuration(site))

    if method == "web.reload":
        _only(params, set())
        return asdict(controller.reload_web())

    if method == "worker.logs":
        _only(params, {"site", "worker", "lines"})
        site = _required_string(params, "site")
        worker = _worker(params)
        lines = params.get("lines", 200)
        if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= 5000:
            raise RequestError("invalid_params", "lines must be an integer from 1 to 5000")
        operation = getattr(controller, f"{worker}_logs")
        return {"lines": list(operation(site, lines))}

    raise RequestError("unknown_method", f"unknown method: {method}")


def _parse_request(line: str) -> tuple[int | None, str, dict[str, object]]:
    try:
        request = json.loads(line)
    except json.JSONDecodeError as error:
        raise RequestError("invalid_request", f"invalid JSON: {error.msg}") from error
    if not isinstance(request, dict):
        raise RequestError("invalid_request", "request must be a JSON object")

    request_id = request.get("id")
    if isinstance(request_id, bool) or not isinstance(request_id, int) or request_id < 0:
        raise RequestError("invalid_request", "id must be a non-negative integer")
    unexpected = sorted(set(request) - {"protocol_version", "id", "method", "params"})
    if unexpected:
        raise RequestError(
            "invalid_request", f"unexpected request field: {unexpected[0]}", request_id
        )
    if request.get("protocol_version") != PROTOCOL_VERSION:
        raise RequestError(
            "incompatible_protocol",
            f"protocol_version must be {PROTOCOL_VERSION}",
            request_id,
        )
    method = request.get("method")
    if not isinstance(method, str) or not method:
        raise RequestError(
            "invalid_request", "method must be a non-empty string", request_id
        )
    params = request.get("params")
    if not isinstance(params, dict):
        raise RequestError("invalid_request", "params must be a JSON object", request_id)
    return request_id, method, params


def _write(output: IO[str], response: dict[str, Any]) -> None:
    output.write(json.dumps(response, separators=(",", ":"), sort_keys=True) + "\n")
    output.flush()


def serve(
    input_stream: IO[str],
    output_stream: IO[str],
    controller: PaddockController,
    palette_path: Path | None = None,
) -> None:
    """Serve requests until stdin closes; every input line gets one response."""
    for line in input_stream:
        request_id: int | None = None
        try:
            request_id, method, params = _parse_request(line)
            result = dispatch(controller, method, params, palette_path)
            _write(output_stream, {"id": request_id, "ok": True, "result": result})
        except RequestError as error:
            if error.request_id is not None:
                request_id = error.request_id
            _write(output_stream, {
                "id": request_id,
                "ok": False,
                "error": {"code": error.code, "message": str(error)},
            })
        except (OSError, RuntimeError, ValueError) as error:
            _write(output_stream, {
                "id": request_id,
                "ok": False,
                "error": {"code": "operation_failed", "message": str(error)},
            })
        except Exception as error:  # pragma: no cover - last-resort process boundary
            traceback.print_exc(file=sys.stderr)
            _write(output_stream, {
                "id": request_id,
                "ok": False,
                "error": {"code": "internal_error", "message": str(error)},
            })


def main() -> int:
    paths = Paths.from_environment()
    store = StateStore(paths)
    store.initialize()
    palette_path = (
        paths.home / ".local/state/omarchy/current/theme/colors.toml"
        if paths.home is not None
        else None
    )
    serve(sys.stdin, sys.stdout, PaddockController(store), palette_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
