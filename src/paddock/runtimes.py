from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re

from .state import StateStore


PHP_MINOR = re.compile(r"^[0-9]+\.[0-9]+$")


class RuntimeError(ValueError):
    pass


@dataclass(frozen=True)
class Runtime:
    version: str
    path: Path
    sha256: str


class RuntimeRegistry:
    def __init__(self, store: StateStore):
        self.store = store

    def list(self) -> list[Runtime]:
        records = self.store.read("runtimes")["runtimes"]
        return [
            Runtime(version, Path(record["path"]), record["sha256"])
            for version, record in sorted(records.items(), key=lambda item: _version_key(item[0]))
        ]

    def register(self, version: str, executable: Path, sha256: str | None = None) -> Runtime:
        version = normalize_minor(version)
        path = executable.expanduser().resolve(strict=True)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeError(f"PHP {version} is not executable: {path}")
        digest = sha256 or file_sha256(path)
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeError("runtime sha256 must be 64 lowercase hexadecimal characters")

        def add(value: dict) -> dict:
            records = dict(value["runtimes"])
            records[version] = {
                "path": str(path),
                "version": version,
                "sha256": digest,
            }
            return {"schema_version": value["schema_version"], "runtimes": records}

        self.store.update("runtimes", add)
        return Runtime(version, path, digest)

    def remove(self, version: str) -> None:
        version = normalize_minor(version)

        def discard(value: dict) -> dict:
            records = dict(value["runtimes"])
            if version not in records:
                raise RuntimeError(f"PHP {version} is not registered")
            del records[version]
            return {"schema_version": value["schema_version"], "runtimes": records}

        self.store.update("runtimes", discard)

    def resolve(self, version: str) -> Runtime:
        version = normalize_minor(version)
        records = {runtime.version: runtime for runtime in self.list()}
        try:
            runtime = records[version]
        except KeyError as error:
            raise RuntimeError(
                f"PHP {version} is not installed. Run: paddock php install {version}"
            ) from error
        if not runtime.path.is_file() or not os.access(runtime.path, os.X_OK):
            raise RuntimeError(f"managed PHP {version} is not executable: {runtime.path}")
        return runtime


def normalize_minor(version: str) -> str:
    if not PHP_MINOR.fullmatch(version):
        raise RuntimeError(f"invalid PHP minor version: {version!r}")
    return version


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _version_key(version: str) -> tuple[int, int]:
    major, minor = version.split(".")
    return int(major), int(minor)
