from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
from urllib.request import urlopen

from .atomic import exclusive_lock
from .state import StateStore


class NodeRuntimeError(RuntimeError):
    pass


def normalize_major(value: str) -> str:
    value = value.removeprefix("v")
    if not re.fullmatch(r"[0-9]+", value):
        raise NodeRuntimeError(f"invalid Node major version: {value!r}")
    return str(int(value))


@dataclass(frozen=True)
class NodeArtifact:
    node: str
    major: str
    architecture: str
    url: str
    sha256: str


class NodeManifest:
    def __init__(self, artifacts: tuple[NodeArtifact, ...]):
        self.artifacts = artifacts

    @classmethod
    def load(cls, path: Path) -> "NodeManifest":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise NodeRuntimeError(f"cannot read Node artifact catalog {path}: {error}") from error
        if not isinstance(raw, dict) or set(raw) != {"schema_version", "artifacts"} or raw["schema_version"] != 1:
            raise NodeRuntimeError("invalid Node artifact catalog")
        found = []
        for item in raw["artifacts"]:
            expected = {"node", "major", "architecture", "url", "sha256"}
            if not isinstance(item, dict) or set(item) != expected:
                raise NodeRuntimeError("invalid Node artifact entry")
            if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", item["node"]):
                raise NodeRuntimeError("invalid Node patch version")
            if item["node"].split(".")[0] != normalize_major(item["major"]):
                raise NodeRuntimeError("Node patch and major versions disagree")
            if item["architecture"] not in {"x86_64", "aarch64"} or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                raise NodeRuntimeError("invalid Node artifact architecture or checksum")
            found.append(NodeArtifact(**item))
        return cls(tuple(found))

    def select(self, major: str) -> NodeArtifact:
        architecture = {"amd64":"x86_64", "arm64":"aarch64"}.get(platform.machine(), platform.machine())
        candidates = [a for a in self.artifacts if a.major == normalize_major(major) and a.architecture == architecture]
        if not candidates:
            raise NodeRuntimeError(f"no Node {major} artifact for {architecture}")
        return max(candidates, key=lambda a: tuple(map(int, a.node.split("."))))


@dataclass(frozen=True)
class NodeRuntime:
    version: str
    path: Path
    sha256: str


class NodeRegistry:
    def __init__(self, store: StateStore):
        self.store = store

    def list(self) -> list[NodeRuntime]:
        records = self.store.read("node_runtimes")["runtimes"]
        return [
            NodeRuntime(version, Path(record["path"]), record["sha256"])
            for version, record in sorted(records.items(), key=lambda item: int(item[0]))
        ]

    def resolve(self, major: str) -> NodeRuntime:
        version = normalize_major(major)
        for runtime in self.list():
            if runtime.version == version and runtime.path.is_file():
                return runtime
        raise NodeRuntimeError(f"Node {version} is not installed. Run: paddock node install {version}")

    def register(self, major: str, path: Path, sha256: str) -> None:
        version = normalize_major(major)
        self.store.update(
            "node_runtimes",
            lambda value: {
                **value,
                "runtimes": {
                    **value["runtimes"],
                    version: {
                        "version": version,
                        "path": str(path.resolve()),
                        "sha256": sha256,
                    },
                },
            },
        )

    def remove(self, major: str) -> None:
        version = normalize_major(major)
        self.store.update(
            "node_runtimes",
            lambda value: {
                **value,
                "runtimes": {
                    key: record
                    for key, record in value["runtimes"].items()
                    if key != version
                },
            },
        )


class NodeInstaller:
    def __init__(self, store: StateStore):
        self.store, self.paths, self.registry = store, store.paths, NodeRegistry(store)

    def install(self, major: str, manifest: NodeManifest) -> Path:
        artifact = manifest.select(major)
        with exclusive_lock(self.paths.state / "node-install.lock"):
            archive = self.paths.cache / "artifacts" / f"{artifact.sha256}.tar.xz"
            archive.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not archive.exists() or _sha256(archive) != artifact.sha256:
                temporary = archive.with_suffix(".part")
                temporary.unlink(missing_ok=True)
                try:
                    with urlopen(artifact.url) as response, temporary.open("wb") as output: shutil.copyfileobj(response, output)
                    if _sha256(temporary) != artifact.sha256: raise NodeRuntimeError("Node artifact checksum mismatch")
                    os.replace(temporary, archive)
                finally: temporary.unlink(missing_ok=True)
            release = self.paths.data / "node-runtimes/releases" / f"node-{artifact.node}-{artifact.sha256[:12]}"
            if not release.exists():
                release.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                staging = Path(tempfile.mkdtemp(prefix=".node-", dir=release.parent))
                try:
                    with tarfile.open(archive, "r:xz") as source:
                        source.extractall(staging, filter="data")
                    roots = [p for p in staging.iterdir() if (p / "bin/node").is_file()]
                    if len(roots) != 1:
                        raise NodeRuntimeError("Node archive must contain one runtime root")
                    os.replace(roots[0], release)
                finally: shutil.rmtree(staging, ignore_errors=True)
            result = subprocess.run([str(release / "bin/node"), "--version"], text=True, capture_output=True, check=False)
            if result.returncode or result.stdout.strip() != f"v{artifact.node}":
                raise NodeRuntimeError("Node runtime validation failed")
            active = self.paths.data / "node-runtimes/active"
            active.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary_link = active / f".{artifact.major}.next"
            temporary_link.unlink(missing_ok=True)
            temporary_link.symlink_to(release)
            os.replace(temporary_link, active / artifact.major)
            self.registry.register(artifact.major, release / "bin/node", artifact.sha256)
            return release

    def remove(self, major: str) -> None:
        version = normalize_major(major)
        users = [name for name, site in self.store.read("sites")["sites"].items() if site.get("node") == version]
        if users:
            raise NodeRuntimeError(
                f"Node {version} is used by: "
                f"{', '.join(name + '.test' for name in users)}"
            )
        link = self.paths.data / "node-runtimes/active" / version
        release = link.resolve(strict=False) if link.is_symlink() else None
        link.unlink(missing_ok=True)
        self.registry.remove(version)
        if release and release.is_dir():
            shutil.rmtree(release)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""): digest.update(block)
    return digest.hexdigest()
