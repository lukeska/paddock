from __future__ import annotations

from dataclasses import dataclass
import json
import re
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
            selected = _read_project(config).get("php")
            if selected is not None:
                candidates.append((distance, 0, Selection(selected, f"project configuration {config}")))
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
    value = _read_project(path) if path.exists() else {}
    value["php"] = selected
    atomic_write(path, (json.dumps(value, indent=2) + "\n").encode())
    return path


def select_node(directory: Path, store: StateStore) -> Selection:
    current = directory.expanduser().resolve(strict=True)
    for parent in (current, *current.parents):
        config = parent / PROJECT_FILE
        if config.is_file():
            selected = _read_project(config).get("node")
            if selected: return Selection(selected, f"project configuration {config}")
            break
    for record in store.read("sites")["sites"].values():
        try: current.relative_to(Path(record["root"]).resolve(strict=True))
        except (FileNotFoundError, ValueError): continue
        if record.get("node"): return Selection(record["node"], f"linked site {record['name']}")
    for parent in (current, *current.parents):
        for name in (".nvmrc", ".node-version"):
            path = parent / name
            if path.is_file():
                value = path.read_text(encoding="utf-8").strip().removeprefix("v")
                major = value.split(".")[0]
                if major.isdigit(): return Selection(str(int(major)), str(path))
        package = parent / "package.json"
        if package.is_file():
            try: engine = json.loads(package.read_text(encoding="utf-8")).get("engines", {}).get("node")
            except (OSError, json.JSONDecodeError, AttributeError): engine = None
            if isinstance(engine, str):
                match = re.search(r"[0-9]+", engine)
                if match: return Selection(str(int(match.group())), f"package.json engines.node ({package})")
    default = store.read("settings").get("default_node")
    if default is None: raise ProjectError("no Node version selected; run paddock node use VERSION")
    return Selection(default, "configured default")


def write_node_selection(directory: Path, version: str) -> Path:
    from .node_runtime import normalize_major
    root = directory.expanduser().resolve(strict=True)
    path = root / PROJECT_FILE
    value = _read_project(path) if path.exists() else {}
    value["node"] = normalize_major(version)
    atomic_write(path, (json.dumps(value, indent=2) + "\n").encode())
    return path


def _read_project(path: Path) -> dict[str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProjectError(f"cannot read project configuration {path}: {error}") from error
    if not isinstance(value, dict) or not set(value) <= {"php", "node"} or not value:
        raise ProjectError(f"project configuration may contain only php and node fields: {path}")
    result = {}
    if "php" in value:
        if not isinstance(value["php"], str): raise ProjectError(f"project PHP version must be a string: {path}")
        result["php"] = normalize_minor(value["php"])
    if "node" in value:
        if not isinstance(value["node"], str) or not value["node"].isdigit(): raise ProjectError(f"project Node version must be a major string: {path}")
        result["node"] = str(int(value["node"]))
    return result
