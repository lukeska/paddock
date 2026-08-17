from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .atomic import atomic_write
from .runtimes import normalize_minor
from .state import StateStore


PROJECT_FILE = ".paddock.json"


class ProjectError(ValueError):
    pass


@dataclass(frozen=True)
class Selection:
    version: str
    source: str


def select_php(directory: Path, store: StateStore) -> Selection:
    try:
        current = directory.expanduser().resolve(strict=True)
    except OSError as error:
        raise ProjectError(f"cannot resolve working directory {directory}: {error}") from error
    if not current.is_dir():
        raise ProjectError(f"working directory is not a directory: {current}")

    candidates: list[tuple[int, int, Selection]] = []
    for distance, parent in enumerate((current, *current.parents)):
        config = parent / PROJECT_FILE
        if config.is_file():
            candidates.append((distance, 0, _read_project(config)))
            break

    sites = store.read("sites")["sites"]
    for record in sites.values():
        try:
            root = Path(record["root"]).resolve(strict=True)
            relative = current.relative_to(root)
        except FileNotFoundError as error:
            raise ProjectError(f"linked site root does not exist: {record['root']}") from error
        except ValueError:
            continue
        candidates.append(
            (
                len(relative.parts),
                1,
                Selection(normalize_minor(record["php"]), f"linked site {record['name']} ({root})"),
            )
        )

    if candidates:
        return min(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2]
    default = store.read("settings")["default_php"]
    if default is None:
        raise ProjectError(
            "no PHP version selected; run paddock php use VERSION or configure a default"
        )
    return Selection(normalize_minor(default), "configured default")


def write_project_selection(directory: Path, version: str) -> Path:
    root = directory.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ProjectError(f"project path is not a directory: {root}")
    selected = normalize_minor(version)
    path = root / PROJECT_FILE
    atomic_write(path, (json.dumps({"php": selected}, indent=2) + "\n").encode())
    return path


def _read_project(path: Path) -> Selection:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProjectError(f"cannot read project configuration {path}: {error}") from error
    if not isinstance(value, dict) or set(value) != {"php"}:
        raise ProjectError(f"project configuration must contain only a php field: {path}")
    version = value["php"]
    if not isinstance(version, str):
        raise ProjectError(f"project PHP version must be a string: {path}")
    return Selection(normalize_minor(version), f"project configuration {path}")
