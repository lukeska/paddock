"""Strict schema for future signed, independently published runtime catalogs.

Parsing does not establish trust. The refresh client in Unit 2 must verify the
detached signature over the original bytes *before* calling this parser.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Literal
from urllib.parse import urlsplit

from .artifacts import ArtifactManifest, ManifestError
from .node_runtime import NodeManifest, NodeRuntimeError


CatalogKind = Literal["php", "node"]
CatalogManifest = ArtifactManifest | NodeManifest

CATALOG_URLS = {
    "php": "https://lukeska.github.io/paddock/runtime-catalogs/php.json",
    "node": "https://lukeska.github.io/paddock/runtime-catalogs/node.json",
}


class RuntimeCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeCatalog:
    kind: CatalogKind
    revision: int
    manifest: CatalogManifest

    @classmethod
    def load(cls, path: Path, kind: CatalogKind) -> "RuntimeCatalog":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeCatalogError(f"cannot read {kind} runtime catalog: {error}") from error
        if kind not in CATALOG_URLS:
            raise RuntimeCatalogError(f"unsupported runtime catalog kind: {kind}")
        if not isinstance(raw, dict) or set(raw) != {"schema_version", "revision", "artifacts"}:
            raise RuntimeCatalogError("runtime catalog has invalid fields")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1 or type(raw["revision"]) is not int or raw["revision"] < 1:
            raise RuntimeCatalogError("runtime catalog has invalid schema version or revision")
        try:
            document = {"schema_version": 1, "artifacts": raw["artifacts"]}
            manifest = (
                ArtifactManifest.from_document(document)
                if kind == "php" else NodeManifest.from_document(document)
            )
        except (ManifestError, NodeRuntimeError, TypeError, AttributeError) as error:
            raise RuntimeCatalogError(f"invalid {kind} artifact: {error}") from error
        if not manifest.artifacts:
            raise RuntimeCatalogError("runtime catalog must contain artifacts")
        seen: set[tuple[str, str]] = set()
        for artifact in manifest.artifacts:
            line = artifact.minor if kind == "php" else artifact.major
            key = (line, artifact.architecture)
            if key in seen:
                raise RuntimeCatalogError(f"duplicate {kind} {line} for {artifact.architecture}")
            seen.add(key)
            _validate_artifact_url(kind, artifact)
        return cls(kind, raw["revision"], manifest)


def _validate_artifact_url(kind: CatalogKind, artifact: object) -> None:
    url = artifact.url
    if not isinstance(url, str) or url != url.strip() or any(ord(char) < 32 for char in url):
        raise RuntimeCatalogError(f"{kind} artifact requires a plain HTTPS URL")
    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise RuntimeCatalogError(f"malformed {kind} artifact URL: {url}") from error
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeCatalogError(f"{kind} artifact requires a plain HTTPS URL: {url}")
    if kind == "php":
        filename = f"paddock-php-{artifact.php}-linux-{artifact.architecture}.tar.gz"
        pattern = rf"/lukeska/paddock/releases/download/php-[0-9]{{4}}\.[0-9]{{2}}\.[0-9]{{2}}/{re.escape(filename)}"
        if parsed.netloc != "github.com" or not re.fullmatch(pattern, parsed.path):
            raise RuntimeCatalogError(f"PHP artifact must name an immutable Paddock release: {url}")
    else:
        arch = {"x86_64": "x64", "aarch64": "arm64"}[artifact.architecture]
        filename = f"node-v{artifact.node}-linux-{arch}.tar.xz"
        path = f"/download/release/v{artifact.node}/{filename}"
        if parsed.netloc != "nodejs.org" or parsed.path != path:
            raise RuntimeCatalogError(f"Node artifact must name an exact official release: {url}")
