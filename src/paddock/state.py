from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .atomic import atomic_write, exclusive_lock
from .paths import Paths
from .schemas import DEFAULTS, VALIDATORS, SchemaError


class StateError(RuntimeError):
    pass


class StateStore:
    def __init__(self, paths: Paths):
        self.paths = paths

    def path_for(self, record: str) -> Path:
        if record == "runtimes":
            return self.paths.data / "runtimes.json"
        if record in {"settings", "sites"}:
            return self.paths.config / f"{record}.json"
        raise StateError(f"unknown state record: {record}")

    def initialize(self) -> None:
        self.paths.initialize()
        for record in DEFAULTS:
            path = self.path_for(record)
            with exclusive_lock(path.with_suffix(path.suffix + ".lock")):
                if not path.exists():
                    self._write_unlocked(record, DEFAULTS[record]())

    def read(self, record: str) -> dict[str, Any]:
        validator = self._validator(record)
        path = self.path_for(record)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return validator(raw)
        except FileNotFoundError as error:
            raise StateError(f"state record does not exist: {path}") from error
        except (OSError, json.JSONDecodeError, SchemaError) as error:
            raise StateError(f"cannot read {record} state at {path}: {error}") from error

    def update(
        self, record: str, transform: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> dict[str, Any]:
        path = self.path_for(record)
        with exclusive_lock(path.with_suffix(path.suffix + ".lock")):
            current = self.read(record)
            candidate = transform(current)
            self._write_unlocked(record, candidate)
            return candidate

    def write(self, record: str, value: dict[str, Any]) -> None:
        path = self.path_for(record)
        with exclusive_lock(path.with_suffix(path.suffix + ".lock")):
            self._write_unlocked(record, value)

    def _write_unlocked(self, record: str, value: dict[str, Any]) -> None:
        validator = self._validator(record)
        validated = validator(value)
        payload = (json.dumps(validated, indent=2, sort_keys=True) + "\n").encode()
        atomic_write(self.path_for(record), payload)

    @staticmethod
    def _validator(record: str):
        try:
            return VALIDATORS[record]
        except KeyError as error:
            raise StateError(f"unknown state record: {record}") from error

