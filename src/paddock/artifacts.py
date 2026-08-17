from __future__ import annotations

from dataclasses import dataclass
import json
import platform
from pathlib import Path
import re
from typing import Any

from .runtimes import normalize_minor


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Artifact:
    php: str
    minor: str
    architecture: str
    url: str
    sha256: str


class ArtifactManifest:
    def __init__(self, artifacts: tuple[Artifact, ...]):
        self.artifacts = artifacts

    @classmethod
    def load(cls, path: Path) -> "ArtifactManifest":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ManifestError(f"cannot read artifact manifest {path}: {error}") from error
        if not isinstance(raw, dict) or set(raw) != {"schema_version", "artifacts"}:
            raise ManifestError("artifact manifest must contain schema_version and artifacts")
        if raw["schema_version"] != 1 or not isinstance(raw["artifacts"], list):
            raise ManifestError("unsupported artifact manifest schema")
        artifacts = tuple(_artifact(value, index) for index, value in enumerate(raw["artifacts"]))
        return cls(artifacts)

    def select(self, minor: str, architecture: str | None = None) -> Artifact:
        selected_minor = normalize_minor(minor)
        selected_arch = architecture or normalized_architecture()
        candidates = [
            artifact
            for artifact in self.artifacts
            if artifact.minor == selected_minor and artifact.architecture == selected_arch
        ]
        if not candidates:
            raise ManifestError(
                f"no PHP {selected_minor} artifact for architecture {selected_arch}"
            )
        return max(candidates, key=lambda artifact: _patch_key(artifact.php))


def normalized_architecture(machine: str | None = None) -> str:
    value = (machine or platform.machine()).lower()
    aliases = {"amd64": "x86_64", "arm64": "aarch64"}
    normalized = aliases.get(value, value)
    if normalized not in {"x86_64", "aarch64"}:
        raise ManifestError(f"unsupported architecture: {value}")
    return normalized


def _artifact(raw: Any, index: int) -> Artifact:
    if not isinstance(raw, dict):
        raise ManifestError(f"artifact {index} must be an object")
    expected = {"php", "minor", "architecture", "url", "sha256"}
    if set(raw) != expected or not all(isinstance(raw[field], str) for field in expected):
        raise ManifestError(f"artifact {index} has invalid fields")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", raw["php"]):
        raise ManifestError(f"artifact {index} has invalid PHP patch version")
    if ".".join(raw["php"].split(".")[:2]) != normalize_minor(raw["minor"]):
        raise ManifestError(f"artifact {index} PHP and minor versions disagree")
    normalized_architecture(raw["architecture"])
    if not re.fullmatch(r"[0-9a-f]{64}", raw["sha256"]):
        raise ManifestError(f"artifact {index} has invalid sha256")
    if not raw["url"]:
        raise ManifestError(f"artifact {index} has an empty URL")
    return Artifact(raw["php"], raw["minor"], raw["architecture"], raw["url"], raw["sha256"])


def _patch_key(version: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]
