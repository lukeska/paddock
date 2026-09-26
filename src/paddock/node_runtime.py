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
from .runtime_inventory import installed_patch


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
        return cls.from_document(raw)

    @classmethod
    def from_document(cls, raw: object) -> "NodeManifest":
        if not isinstance(raw, dict) or set(raw) != {"schema_version", "artifacts"} or raw["schema_version"] != 1:
            raise NodeRuntimeError("invalid Node artifact catalog")
        if not isinstance(raw["artifacts"], list):
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
    release: str | None = None
    previous_release: str | None = None
    previous_sha256: str | None = None


class NodeRegistry:
    def __init__(self, store: StateStore):
        self.store = store

    def list(self) -> list[NodeRuntime]:
        records = self.store.read("node_runtimes")["runtimes"]
        missing = {version: record for version, record in records.items() if "release" not in record}
        if missing:
            releases = self.store.paths.data / "node-runtimes/releases"
            recovered = {
                version: installed_patch(Path(record["path"]), record["sha256"], releases, "node", version)
                for version, record in missing.items()
            }
            records = self.store.update("node_runtimes", lambda value: {
                **value,
                "runtimes": {
                    version: ({**record, "release": recovered[version]}
                              if version in recovered and "release" not in record
                              and record == missing[version] else record)
                    for version, record in value["runtimes"].items()
                },
            })["runtimes"]
        return [
            NodeRuntime(version, Path(record["path"]), record["sha256"], record.get("release"),
                        record.get("previous_release"), record.get("previous_sha256"))
            for version, record in sorted(records.items(), key=lambda item: int(item[0]))
        ]

    def resolve(self, major: str) -> NodeRuntime:
        version = normalize_major(major)
        for runtime in self.list():
            if runtime.version == version and runtime.path.is_file():
                return runtime
        raise NodeRuntimeError(f"Node {version} is not installed. Run: paddock node install {version}")

    def register(self, major: str, path: Path, sha256: str, release: str | None = None,
                 previous_release: str | None = None, previous_sha256: str | None = None) -> None:
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
                        "release": release,
                        "previous_release": previous_release,
                        "previous_sha256": previous_sha256,
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

    def install(self, major: str, manifest: NodeManifest, *, replace_custom: bool = False,
                expected_release: str | None = None) -> Path:
        artifact = manifest.select(major)
        if expected_release is not None and artifact.node != expected_release:
            raise NodeRuntimeError(
                f"Node {major} catalog changed from confirmed {expected_release} to {artifact.node}; confirm again"
            )
        with exclusive_lock(self.paths.state / "node-install.lock"):
            current = next((item for item in self.registry.list() if item.version == artifact.major), None)
            releases = self.paths.data / "node-runtimes/releases"
            active_link = self.paths.data / "node-runtimes/active" / artifact.major
            if current is not None:
                if not self._managed(current.path, current.release, current.sha256, releases):
                    if not replace_custom:
                        raise NodeRuntimeError(
                            f"Node {artifact.major} is custom or has an unknown patch; "
                            "use --replace-custom to replace it explicitly"
                        )
                elif ((current.release, current.sha256) == (artifact.node, artifact.sha256)
                      and active_link.is_symlink()
                      and active_link.resolve() == current.path.parent.parent):
                    return current.path.parent.parent
                elif self._patch_key(artifact.node) < self._patch_key(current.release):
                    raise NodeRuntimeError(
                        f"Node {artifact.node} is older than installed {current.release}; use rollback instead"
                    )
                elif current.release == artifact.node and current.sha256 != artifact.sha256:
                    raise NodeRuntimeError(
                        f"Node {artifact.node} catalog hash differs from the installed release"
                    )
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
            self._validate(release, artifact.node)
            self._switch(artifact.major, release, artifact.node, artifact.sha256, current)
            return release

    def rollback(self, major: str, *, expected_release: str | None = None) -> Path:
        version = normalize_major(major)
        with exclusive_lock(self.paths.state / "node-install.lock"):
            current = self.registry.resolve(version)
            if not current.previous_release or not current.previous_sha256:
                raise NodeRuntimeError(f"Node {version} has no retained previous release")
            if expected_release is not None and current.previous_release != expected_release:
                raise NodeRuntimeError(
                    f"Node {version} rollback target changed from confirmed {expected_release}; confirm again"
                )
            releases = self.paths.data / "node-runtimes/releases"
            if not self._managed(current.path, current.release, current.sha256, releases):
                raise NodeRuntimeError(f"Node {version} active runtime is not managed by Paddock")
            release = releases / f"node-{current.previous_release}-{current.previous_sha256[:12]}"
            if not release.is_dir():
                raise NodeRuntimeError(f"Node {current.previous_release} release is missing")
            self._validate(release, current.previous_release)
            self._switch(version, release, current.previous_release, current.previous_sha256, current)
            return release

    @staticmethod
    def _patch_key(value: str) -> tuple[int, ...]:
        return tuple(int(part) for part in value.split("."))

    @staticmethod
    def _managed(path: Path, release: str | None, digest: str, releases: Path) -> bool:
        return bool(release and path == releases / f"node-{release}-{digest[:12]}" / "bin/node"
                    and path.is_file())

    @staticmethod
    def _validate(release: Path, version: str) -> None:
        result = subprocess.run([str(release / "bin/node"), "--version"], text=True,
                                capture_output=True, check=False)
        if result.returncode or result.stdout.strip() != f"v{version}":
            raise NodeRuntimeError(f"Node {version} runtime validation failed")

    def _switch(self, major: str, release: Path, version: str, digest: str,
                current: NodeRuntime | None) -> None:
        active = self.paths.data / "node-runtimes/active"
        active.mkdir(parents=True, exist_ok=True, mode=0o700)
        link = active / major
        if link.exists() and not link.is_symlink():
            raise NodeRuntimeError(f"Node {major} activation path is not a symlink: {link}")
        previous_target = link.readlink() if link.is_symlink() else None
        previous_registry = self.store.read("node_runtimes")
        temporary = active / f".{major}.next"
        try:
            temporary.unlink(missing_ok=True)
            temporary.symlink_to(release)
            os.replace(temporary, link)
            previous_release = None
            previous_sha256 = None
            if current and self._managed(current.path, current.release, current.sha256,
                                         self.paths.data / "node-runtimes/releases"):
                if (current.release, current.sha256) == (version, digest):
                    previous_release, previous_sha256 = current.previous_release, current.previous_sha256
                else:
                    previous_release, previous_sha256 = current.release, current.sha256
            self.registry.register(
                major, release / "bin/node", digest, version,
                previous_release, previous_sha256,
            )
        except (Exception, KeyboardInterrupt) as error:
            recovery = []
            try:
                if previous_target is None:
                    link.unlink(missing_ok=True)
                else:
                    temporary.unlink(missing_ok=True)
                    temporary.symlink_to(previous_target)
                    os.replace(temporary, link)
                self.store.write("node_runtimes", previous_registry)
            except Exception as rollback_error:
                recovery.append(f"rollback incomplete: {rollback_error}")
            raise NodeRuntimeError(
                f"Node {major} activation failed: {error}"
                + ("; " + "; ".join(recovery) if recovery else "")
            ) from error
        finally:
            temporary.unlink(missing_ok=True)

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
