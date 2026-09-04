from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

from .atomic import atomic_write
from .execution import plan_composer, plan_node, plan_php
from .paths import Paths
from .projects import ProjectError, select_node, select_php
from .state import StateError, StateStore


SHIM_DIRECTORY = Path("/usr/lib/paddock/shims")
START = "# >>> paddock shims >>>"
END = "# <<< paddock shims <<<"
BLOCK = f'{START}\nexport PATH="{SHIM_DIRECTORY}:$PATH"\n{END}\n'


class ShellIntegrationError(RuntimeError):
    pass


def install_shell_integration(home: Path) -> bool:
    bashrc = home / ".bashrc"
    if bashrc.is_symlink():
        raise ShellIntegrationError(f"refusing to modify symlinked shell configuration: {bashrc}")
    existing = bashrc.read_text(encoding="utf-8") if bashrc.exists() else ""
    cleaned = _without_block(existing)
    updated = cleaned.rstrip() + ("\n\n" if cleaned.strip() else "") + BLOCK
    if updated == existing:
        return False
    atomic_write(bashrc, updated.encode())
    return True


def remove_shell_integration(home: Path) -> bool:
    bashrc = home / ".bashrc"
    if not bashrc.exists() or bashrc.is_symlink():
        return False
    existing = bashrc.read_text(encoding="utf-8")
    updated = _without_block(existing)
    if updated == existing:
        return False
    atomic_write(bashrc, updated.encode())
    return True


def run_shim(command: str, arguments: list[str] | None = None) -> None:
    if command not in {"php", "composer", "node", "npm", "npx"}:
        raise ShellIntegrationError(f"unsupported Paddock shim: {command}")
    forwarded = sys.argv[1:] if arguments is None else arguments
    store = StateStore(Paths.from_environment())
    try:
        selection = select_node(Path.cwd(), store) if command in {"node", "npm", "npx"} else select_php(Path.cwd(), store)
    except StateError:
        _fallback(command, forwarded)
        return
    except ProjectError as error:
        if str(error).startswith(("no PHP version selected", "no Node version selected")):
            _fallback(command, forwarded)
            return
        raise
    if selection.source == "configured default":
        _fallback(command, forwarded)
        return
    if command == "php": plan = plan_php(Path.cwd(), forwarded, store)
    elif command == "composer": plan = plan_composer(Path.cwd(), forwarded, store)
    else: plan = plan_node(Path.cwd(), command, forwarded, store)
    plan.execute()


def _fallback(command: str, arguments: list[str]) -> None:
    paths = [
        entry for entry in os.environ.get("PATH", "").split(os.pathsep)
        if Path(entry).resolve(strict=False) != SHIM_DIRECTORY
    ]
    executable = shutil.which(command, path=os.pathsep.join(paths))
    if executable is None:
        raise ShellIntegrationError(
            f"{command} is not available outside a Paddock project"
        )
    os.execvpe(executable, (command, *arguments), os.environ)


def _without_block(value: str) -> str:
    start = value.find(START)
    if start == -1:
        return value
    end = value.find(END, start)
    if end == -1:
        raise ShellIntegrationError("Paddock shell integration block is incomplete")
    end += len(END)
    if end < len(value) and value[end] == "\n":
        end += 1
    return value[:start].rstrip() + ("\n" if value[:start].strip() else "") + value[end:].lstrip("\n")
