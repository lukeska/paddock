"""Authenticated, rollback-resistant PHP and Node catalog refresh."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Callable
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from .artifacts import ArtifactManifest, ManifestError
from .atomic import atomic_write, exclusive_lock
from .node_runtime import NodeManifest, NodeRuntimeError
from .paths import Paths
from .runtime_catalog import CATALOG_URLS, CatalogKind, CatalogManifest, RuntimeCatalog, RuntimeCatalogError


PRIMARY_FINGERPRINT = "AB3611DC044DE36844055E9AC1A41BDC59DCEA60"
MAX_CATALOG_BYTES = 1024 * 1024
MAX_SIGNATURE_BYTES = 64 * 1024
PACKAGED_KEY = Path("/usr/share/paddock/release-public.asc")
SOURCE_KEY = Path(__file__).resolve().parents[2] / "docs/paddock-release-public.asc"


class CatalogStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class CatalogSelection:
    manifest: CatalogManifest
    source: str
    revision: int | None
    checked_at: str | None
    warning: str | None = None


def _download(url: str, limit: int) -> bytes:
    request = Request(url, headers={"User-Agent": "Paddock runtime catalog"})
    with urlopen(request, timeout=10) as response:
        final = urlsplit(response.geturl())
        expected = urlsplit(url)
        if final.scheme != "https" or final.netloc != expected.netloc:
            raise CatalogStoreError("catalog redirect left the trusted HTTPS host")
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise CatalogStoreError(f"catalog response exceeds {limit} bytes")
    return payload


class CatalogStore:
    def __init__(
        self,
        paths: Paths,
        *,
        key_path: Path | None = None,
        fingerprint: str = PRIMARY_FINGERPRINT,
        fetch: Callable[[str, int], bytes] = _download,
        gpg: str = "gpg",
    ):
        self.paths = paths
        self.key_path = key_path or (PACKAGED_KEY if PACKAGED_KEY.is_file() else SOURCE_KEY)
        self.fingerprint = fingerprint
        self.fetch = fetch
        self.gpg = gpg

    def _state_path(self, kind: CatalogKind) -> Path:
        return self.paths.state / "runtime-catalogs" / f"{kind}.json"

    def _cache_path(self, kind: CatalogKind, digest: str) -> Path:
        return self.paths.cache / "runtime-catalogs" / f"{kind}-{digest}.json"

    def _metadata(self, kind: CatalogKind) -> dict | None:
        path = self._state_path(kind)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise CatalogStoreError(f"cannot read {kind} catalog state: {error}") from error
        if (
            not isinstance(value, dict)
            or set(value) != {"schema_version", "revision", "sha256", "checked_at"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["revision"]) is not int or value["revision"] < 1
            or not isinstance(value["sha256"], str) or len(value["sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in value["sha256"])
            or not isinstance(value["checked_at"], str)
        ):
            raise CatalogStoreError(f"invalid {kind} catalog state")
        return value

    def _verify(self, payload: bytes, signature: bytes) -> None:
        if not self.key_path.is_file():
            raise CatalogStoreError(f"catalog verification key is missing: {self.key_path}")
        with tempfile.TemporaryDirectory(prefix="paddock-catalog-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            home = root / "gnupg"
            home.mkdir(mode=0o700)
            catalog = root / "catalog.json"
            sig = root / "catalog.json.sig"
            catalog.write_bytes(payload)
            sig.write_bytes(signature)
            try:
                imported = subprocess.run(
                    [self.gpg, "--homedir", str(home), "--no-options", "--batch", "--no-default-keyring",
                     "--import", str(self.key_path)],
                    text=True, capture_output=True, check=False, timeout=15,
                )
                if imported.returncode:
                    raise CatalogStoreError("cannot import packaged catalog verification key")
                result = subprocess.run(
                    [self.gpg, "--homedir", str(home), "--no-options", "--batch", "--no-default-keyring",
                     "--no-auto-key-retrieve",
                     "--status-fd", "1", "--verify", str(sig), str(catalog)],
                    text=True, capture_output=True, check=False, timeout=15,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise CatalogStoreError(f"cannot verify catalog signature: {error}") from error
        valid = [line.split() for line in result.stdout.splitlines()
                 if line.startswith("[GNUPG:] VALIDSIG ")]
        rejected = ("EXPKEYSIG", "REVKEYSIG", "EXPSIG", "KEYEXPIRED", "SIGEXPIRED", "BADSIG", "ERRSIG")
        if (result.returncode or len(valid) != 1
                or valid[0][-1].upper() != self.fingerprint.upper()
                or any(f"[GNUPG:] {status} " in result.stdout for status in rejected)):
            raise CatalogStoreError("catalog signature is invalid or not from the pinned release key")

    def refresh(self, kind: CatalogKind) -> CatalogSelection:
        if kind not in CATALOG_URLS:
            raise CatalogStoreError(f"unsupported catalog kind: {kind}")
        url = CATALOG_URLS[kind]
        with exclusive_lock(self._state_path(kind).with_suffix(".lock")):
            previous = self._metadata(kind)
            payload = self.fetch(url, MAX_CATALOG_BYTES)
            signature = self.fetch(url + ".sig", MAX_SIGNATURE_BYTES)
            if len(payload) > MAX_CATALOG_BYTES or len(signature) > MAX_SIGNATURE_BYTES:
                raise CatalogStoreError("catalog or signature exceeds size limit")
            self._verify(payload, signature)
            with tempfile.TemporaryDirectory(prefix="paddock-schema-") as temporary:
                path = Path(temporary) / "catalog.json"
                path.write_bytes(payload)
                catalog = RuntimeCatalog.load(path, kind)
            digest = hashlib.sha256(payload).hexdigest()
            if previous is not None:
                if catalog.revision < previous["revision"]:
                    raise CatalogStoreError(f"{kind} catalog revision {catalog.revision} is older than accepted revision {previous['revision']}")
                if catalog.revision == previous["revision"] and digest != previous["sha256"]:
                    raise CatalogStoreError(f"{kind} catalog revision {catalog.revision} changed bytes")
            cache = self._cache_path(kind, digest)
            atomic_write(cache, payload)
            atomic_write(cache.with_suffix(".json.sig"), signature)
            checked_at = datetime.now(timezone.utc).isoformat()
            metadata = {"schema_version": 1, "revision": catalog.revision,
                        "sha256": digest, "checked_at": checked_at}
            atomic_write(self._state_path(kind), (json.dumps(metadata, sort_keys=True) + "\n").encode())
            return CatalogSelection(catalog.manifest, "refreshed", catalog.revision, checked_at)

    def _cached(self, kind: CatalogKind) -> CatalogSelection | None:
        metadata = self._metadata(kind)
        if metadata is None:
            return None
        cache = self._cache_path(kind, metadata["sha256"])
        try:
            payload = cache.read_bytes()
            signature = cache.with_suffix(".json.sig").read_bytes()
        except OSError as error:
            raise CatalogStoreError(f"cached {kind} catalog is missing: {error}") from error
        if hashlib.sha256(payload).hexdigest() != metadata["sha256"]:
            raise CatalogStoreError(f"cached {kind} catalog digest mismatch")
        self._verify(payload, signature)
        try:
            with tempfile.TemporaryDirectory(prefix="paddock-schema-") as temporary:
                path = Path(temporary) / "catalog.json"
                path.write_bytes(payload)
                catalog = RuntimeCatalog.load(path, kind)
        except RuntimeCatalogError as error:
            raise CatalogStoreError(f"cached {kind} catalog is invalid: {error}") from error
        if catalog.revision != metadata["revision"]:
            raise CatalogStoreError(f"cached {kind} catalog revision mismatch")
        return CatalogSelection(catalog.manifest, "refreshed", catalog.revision, metadata["checked_at"])

    def effective(self, kind: CatalogKind, *, bundled: tuple[Path, ...] | None = None) -> CatalogSelection:
        override = self.paths.config / ("artifacts.json" if kind == "php" else "node-artifacts.json")
        if override.is_file():
            try:
                manifest = ArtifactManifest.load(override) if kind == "php" else NodeManifest.load(override)
                return CatalogSelection(manifest, "local override", None, None)
            except (ManifestError, NodeRuntimeError) as error:
                warning = f"invalid local {kind} catalog: {error}"
        else:
            warning = None
        try:
            cached = self._cached(kind)
            if cached is not None:
                return CatalogSelection(cached.manifest, cached.source, cached.revision,
                                        cached.checked_at, warning)
        except CatalogStoreError as error:
            warning = "; ".join(filter(None, (warning, str(error))))
        paths = bundled or ((Path("/usr/share/paddock/artifacts.json"), Path(__file__).resolve().parents[2] / "resources/artifacts.json")
                            if kind == "php" else (Path("/usr/share/paddock/node-artifacts.json"), Path(__file__).resolve().parents[2] / "resources/node-artifacts.json"))
        for path in paths:
            if path.is_file():
                try:
                    manifest = ArtifactManifest.load(path) if kind == "php" else NodeManifest.load(path)
                    return CatalogSelection(manifest, "bundled", None, None, warning)
                except (ManifestError, NodeRuntimeError):
                    continue
        raise CatalogStoreError(warning or f"no usable {kind} runtime catalog")

    def status(self, kind: CatalogKind) -> dict:
        try:
            selection = self.effective(kind)
            return {"kind": kind, "source": selection.source, "revision": selection.revision,
                    "checked_at": selection.checked_at, "warning": selection.warning}
        except CatalogStoreError as error:
            return {"kind": kind, "source": "unavailable", "revision": None,
                    "checked_at": None, "warning": str(error)}
