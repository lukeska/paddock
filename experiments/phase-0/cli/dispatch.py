#!/usr/bin/env python3

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any


PROJECT_FILE = ".paddock.json"


class DispatchError(Exception):
    pass


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise DispatchError(f"{label} does not exist: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise DispatchError(f"Cannot read {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise DispatchError(f"{label} must contain a JSON object: {path}")
    return value


def ancestor_distance(directory: Path, ancestor: Path) -> int | None:
    try:
        relative = directory.relative_to(ancestor)
    except ValueError:
        return None
    return len(relative.parts)


def project_candidate(directory: Path) -> tuple[int, int, str, str] | None:
    for distance, candidate_dir in enumerate((directory, *directory.parents)):
        project_file = candidate_dir / PROJECT_FILE
        if not project_file.is_file():
            continue
        project = load_json(project_file, "project configuration")
        version = project.get("php")
        if not isinstance(version, str) or not version:
            raise DispatchError(f"Project configuration has no PHP version: {project_file}")
        return distance, 0, version, str(project_file)
    return None


def linked_candidate(
    directory: Path, sites: object
) -> tuple[int, int, str, str] | None:
    if not isinstance(sites, list):
        raise DispatchError("Registry field 'sites' must be an array")
    candidates: list[tuple[int, int, str, str]] = []
    for index, site in enumerate(sites):
        if not isinstance(site, dict):
            raise DispatchError(f"Registry site {index} must be an object")
        root_value = site.get("root")
        version = site.get("php")
        if not isinstance(root_value, str) or not isinstance(version, str):
            raise DispatchError(f"Registry site {index} requires string root and php fields")
        root = Path(root_value).expanduser().resolve(strict=True)
        distance = ancestor_distance(directory, root)
        if distance is not None:
            candidates.append((distance, 1, version, f"linked site {root}"))
    return min(candidates) if candidates else None


def select(directory: Path, registry: dict[str, Any]) -> tuple[str, str]:
    candidates = [
        candidate
        for candidate in (
            project_candidate(directory),
            linked_candidate(directory, registry.get("sites", [])),
        )
        if candidate is not None
    ]
    if candidates:
        _, _, version, source = min(candidates)
        return version, source
    default = registry.get("default_php")
    if not isinstance(default, str) or not default:
        raise DispatchError("Registry has no default_php version")
    return default, "configured default"


def parse_arguments(argv: list[str]) -> tuple[Path, str, list[str]]:
    cwd = Path.cwd()
    if len(argv) >= 2 and argv[0] == "--cwd":
        cwd = Path(argv[1])
        argv = argv[2:]
    if not argv or argv[0] not in {"php", "composer"}:
        raise DispatchError("Usage: dispatch.py [--cwd DIR] <php|composer> [--] [arguments...]")
    command = argv[0]
    arguments = argv[1:]
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    try:
        cwd = cwd.expanduser().resolve(strict=True)
    except OSError as error:
        raise DispatchError(f"Cannot resolve working directory {cwd}: {error}") from error
    if not cwd.is_dir():
        raise DispatchError(f"Working directory is not a directory: {cwd}")
    return cwd, command, arguments


def main() -> int:
    registry_value = os.environ.get("PADDOCK_REGISTRY")
    if not registry_value:
        raise DispatchError("PADDOCK_REGISTRY is not set")
    registry_path = Path(registry_value).expanduser().resolve(strict=True)
    registry = load_json(registry_path, "registry")
    cwd, command, arguments = parse_arguments(sys.argv[1:])
    version, source = select(cwd, registry)

    runtimes = registry.get("runtimes")
    if not isinstance(runtimes, dict):
        raise DispatchError("Registry field 'runtimes' must be an object")
    executable_value = runtimes.get(version)
    if not isinstance(executable_value, str):
        raise DispatchError(
            f"PHP {version} selected by {source} is not installed. "
            f"Run: paddock php install {version}"
        )
    executable = Path(executable_value).expanduser().resolve(strict=True)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise DispatchError(f"Managed PHP {version} is not executable: {executable}")

    child_argv = [str(executable)]
    if command == "composer":
        composer_value = registry.get("composer")
        if not isinstance(composer_value, str):
            raise DispatchError("Registry has no Composer path")
        composer = Path(composer_value).expanduser().resolve(strict=True)
        child_argv.append(str(composer))
    child_argv.extend(arguments)
    os.chdir(cwd)
    os.execvpe(str(executable), child_argv, os.environ.copy())
    return 127


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DispatchError as error:
        print(f"paddock: {error}", file=sys.stderr)
        raise SystemExit(78)

