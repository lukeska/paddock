from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping, Sequence

from .projects import Selection, select_php
from .runtimes import RuntimeRegistry
from .state import StateStore


class ExecutionError(ValueError):
    pass


@dataclass(frozen=True)
class ExecutionPlan:
    executable: Path
    arguments: tuple[str, ...]
    cwd: Path
    selection: Selection
    environment: tuple[tuple[str, str], ...] = ()

    def execute(self, environment: Mapping[str, str] | None = None) -> None:
        env = dict(os.environ if environment is None else environment)
        env.update(self.environment)
        os.chdir(self.cwd)
        argv = (str(self.executable), *self.arguments)
        os.execve(self.executable, argv, env)


def plan_php(
    directory: Path, arguments: Sequence[str], store: StateStore
) -> ExecutionPlan:
    cwd = directory.expanduser().resolve(strict=True)
    selection = select_php(cwd, store)
    runtime = RuntimeRegistry(store).resolve(selection.version)
    root = runtime.path.parent.parent
    runtime_environment = (
        ("PHPRC", str(root / "etc" / "php.ini")),
        ("PHP_INI_SCAN_DIR", str(root / "etc" / "conf.d")),
    )
    runtime_arguments = ("-d", f"extension_dir={root / 'modules'}", *arguments)
    return ExecutionPlan(runtime.path, tuple(runtime_arguments), cwd, selection, runtime_environment)


def plan_composer(
    directory: Path,
    arguments: Sequence[str],
    store: StateStore,
    composer: Path | None = None,
) -> ExecutionPlan:
    composer_path = composer or (store.paths.data / "composer" / "composer.phar")
    composer_path = composer_path.expanduser().resolve(strict=True)
    if not composer_path.is_file():
        raise ExecutionError(f"Composer is not installed: {composer_path}")
    php = plan_php(directory, (), store)
    return ExecutionPlan(
        php.executable,
        (*php.arguments[:2], str(composer_path), *arguments),
        php.cwd,
        php.selection,
        php.environment,
    )
