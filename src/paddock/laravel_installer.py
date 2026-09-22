from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from typing import Callable, Sequence

from .execution import ExecutionPlan, plan_default_composer, plan_default_php
from .state import StateStore


class LaravelInstallerError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]
ENVIRONMENT_MARKER = "PADDOCK_LARAVEL"


def install_laravel_installer(
    store: StateStore, runner: Runner = subprocess.run
) -> str | None:
    """Install Laravel's official global installer when the user lacks it.

    The ordinary Composer global home is intentional: Laravel's own update
    prompt and ``composer global update laravel/installer`` must keep working.
    Existing installations belong to the user and are never replaced here.
    """
    existing = _installed_version(store, runner)
    if existing is not None:
        return None
    result = _run_composer(
        store,
        ("global", "require", "laravel/installer", "--no-interaction"),
        runner,
    )
    if result.returncode:
        raise LaravelInstallerError(
            "cannot install Laravel Installer: " + _detail(result)
        )
    version = _installed_version(store, runner)
    if version is None:
        raise LaravelInstallerError(
            "Composer completed but Laravel Installer is not globally installed"
        )
    store.update(
        "settings", lambda value: {**value, "laravel_installer_managed": True}
    )
    return version


def remove_managed_laravel_installer(
    store: StateStore, runner: Runner = subprocess.run
) -> bool:
    """Remove only an installer Paddock recorded as installing itself."""
    if not store.read("settings").get("laravel_installer_managed", False):
        return False
    if _installed_version(store, runner) is None:
        store.update(
            "settings", lambda value: {**value, "laravel_installer_managed": False}
        )
        return False
    result = _run_composer(
        store,
        ("global", "remove", "laravel/installer", "--no-interaction"),
        runner,
    )
    if result.returncode:
        raise LaravelInstallerError(
            "cannot remove Paddock-installed Laravel Installer: " + _detail(result)
        )
    store.update(
        "settings", lambda value: {**value, "laravel_installer_managed": False}
    )
    return True


def plan_laravel(
    directory: Path,
    arguments: Sequence[str],
    store: StateStore,
    runner: Runner = subprocess.run,
) -> ExecutionPlan:
    """Plan Laravel Installer under the default Paddock PHP runtime."""
    php = plan_default_php(directory, (), store)
    major, minor = (int(part) for part in php.selection.version.split("."))
    if (major, minor) < (8, 2):
        raise LaravelInstallerError(
            "Laravel Installer requires PHP 8.2 or newer; "
            "configure a newer default PHP runtime in Paddock"
        )
    result = _run_composer(store, ("global", "config", "bin-dir", "--absolute"), runner)
    if result.returncode:
        raise LaravelInstallerError(
            "cannot locate Composer's global bin directory: " + _detail(result)
        )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise LaravelInstallerError("Composer returned an empty global bin directory")
    bin_directory = Path(lines[-1]).expanduser()
    if not bin_directory.is_absolute():
        raise LaravelInstallerError(
            f"Composer returned a relative global bin directory: {bin_directory}"
        )
    executable = bin_directory / "laravel"
    if not executable.is_file():
        raise LaravelInstallerError(
            "Laravel Installer is not installed; re-run paddock setup"
        )
    environment = dict(php.environment)
    environment[ENVIRONMENT_MARKER] = "1"
    return ExecutionPlan(
        php.executable,
        (*php.arguments[:2], str(executable), *arguments),
        php.cwd,
        php.selection,
        tuple(environment.items()),
    )


def _installed_version(store: StateStore, runner: Runner) -> str | None:
    result = _run_composer(
        store,
        ("global", "show", "laravel/installer", "--format=json"),
        runner,
    )
    if result.returncode:
        return None
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    versions = document.get("versions") if isinstance(document, dict) else None
    candidates = versions if isinstance(versions, list) else [versions]
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        match = re.search(r"[0-9]+\.[0-9]+\.[0-9]+", candidate)
        if match:
            return match.group(0)
    return None


def _run_composer(
    store: StateStore, arguments: Sequence[str], runner: Runner
) -> subprocess.CompletedProcess[str]:
    # A command dispatched through sudo/runuser may inherit /root as its cwd.
    # Global Composer operations have no project context, so anchor them in a
    # Paddock-owned directory that setup has already created.
    plan = plan_default_composer(store.paths.data, arguments, store)
    environment = {**os.environ, **dict(plan.environment)}
    return runner(
        [str(plan.executable), *plan.arguments],
        cwd=plan.cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    return result.stderr.strip() or result.stdout.strip() or "unknown error"
