#!/usr/bin/env python3
"""Prepare, verify, and publish independently signed runtime catalogs.

Signing is intentionally external: this command never accesses a private key.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paddock.atomic import atomic_write  # noqa: E402
from paddock.catalog_store import CatalogStore, PRIMARY_FINGERPRINT, SOURCE_KEY  # noqa: E402
from paddock.paths import Paths  # noqa: E402
from paddock.runtime_catalog import CATALOG_URLS, RuntimeCatalog  # noqa: E402


MAX_ARTIFACT_BYTES = 768 * 1024 * 1024
EXPECTED_COVERAGE = {
    "php": {(f"8.{minor}", "x86_64") for minor in range(6)},
    "node": {(major, arch) for major in ("22", "24") for arch in ("x86_64", "aarch64")},
}


class PromotionError(RuntimeError):
    pass


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise PromotionError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise PromotionError(f"{path} must contain a JSON object")
    return value


def _catalog(payload: bytes, kind: str) -> RuntimeCatalog:
    with tempfile.TemporaryDirectory(prefix="paddock-catalog-parse-") as temporary:
        path = Path(temporary) / "catalog.json"
        path.write_bytes(payload)
        return RuntimeCatalog.load(path, kind)


def _coverage(catalog: RuntimeCatalog) -> None:
    found = {
        (artifact.minor if catalog.kind == "php" else artifact.major, artifact.architecture)
        for artifact in catalog.manifest.artifacts
    }
    if found != EXPECTED_COVERAGE[catalog.kind]:
        missing = sorted(EXPECTED_COVERAGE[catalog.kind] - found)
        extra = sorted(found - EXPECTED_COVERAGE[catalog.kind])
        raise PromotionError(f"{catalog.kind} architecture coverage mismatch: missing={missing}, extra={extra}")
    if catalog.kind == "node":
        by_major: dict[str, set[str]] = {}
        for artifact in catalog.manifest.artifacts:
            by_major.setdefault(artifact.major, set()).add(artifact.node)
        if any(len(patches) != 1 for patches in by_major.values()):
            raise PromotionError("Node architectures must use the same patch for each major")


def _download(url: str, destination: Path, *, limit: int = MAX_ARTIFACT_BYTES,
              final_host: str | None = None) -> str:
    digest = hashlib.sha256()
    size = 0
    request = Request(url, headers={"User-Agent": "Paddock catalog promotion"})
    try:
        with urlopen(request, timeout=30) as response, destination.open("xb") as output:
            final = urlsplit(response.geturl())
            if final.scheme != "https":
                raise PromotionError(f"download left HTTPS: {url}")
            if final_host is not None and final.netloc != final_host:
                raise PromotionError(f"download left {final_host}: {url}")
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > limit:
                    raise PromotionError(f"download exceeds {limit} bytes: {url}")
                digest.update(block)
                output.write(block)
    except (OSError, ValueError) as error:
        raise PromotionError(f"cannot download {url}: {error}") from error
    return digest.hexdigest()


def _php_provenance(archive: Path, digest: str) -> str:
    try:
        result = subprocess.run(
            ["gh", "attestation", "verify", str(archive), "--repo", "lukeska/paddock",
             "--format", "json"], text=True, capture_output=True, check=False, timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PromotionError(f"cannot verify PHP build attestation: {error}") from error
    if result.returncode:
        raise PromotionError(f"PHP build attestation failed: {result.stderr.strip()}")
    try:
        attestations = json.loads(result.stdout)
    except ValueError as error:
        raise PromotionError("PHP build attestation returned invalid JSON") from error
    if not isinstance(attestations, list) or not attestations:
        raise PromotionError("PHP build attestation returned no verified statements")
    for item in attestations:
        try:
            verified = item["verificationResult"]
            certificate = verified["signature"]["certificate"]
            subjects = verified["statement"]["subject"]
            commit = certificate["sourceRepositoryDigest"]
            runner = certificate["runnerEnvironment"]
            signer = certificate["buildSignerURI"]
        except (TypeError, KeyError):
            continue
        if (isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit)
                and runner == "github-hosted"
                and isinstance(signer, str)
                and "lukeska/paddock/.github/workflows/runtime-release.yml" in signer
                and any(subject.get("name") == archive.name
                        and subject.get("digest", {}).get("sha256") == digest
                        for subject in subjects if isinstance(subject, dict))):
            return commit
    raise PromotionError(f"PHP attestation does not match {archive.name}, its digest, or the trusted workflow")


def _node_provenance(artifact: object, digest: str, shasums: dict[str, str]) -> None:
    version = artifact.node
    if version not in shasums:
        url = f"https://nodejs.org/download/release/v{version}/SHASUMS256.txt"
        with tempfile.TemporaryDirectory(prefix="paddock-node-sums-") as temporary:
            path = Path(temporary) / "SHASUMS256.txt"
            _download(url, path, limit=2 * 1024 * 1024, final_host="nodejs.org")
            shasums[version] = path.read_text(encoding="utf-8")
    filename = urlsplit(artifact.url).path.rsplit("/", 1)[-1]
    matches = [line.split() for line in shasums[version].splitlines()
               if len(line.split()) == 2 and line.split()[1] == filename]
    if len(matches) != 1 or matches[0][0] != digest:
        raise PromotionError(f"official Node SHASUMS256.txt does not pin {filename} to {digest}")


def validate_artifacts(catalog: RuntimeCatalog) -> list[str]:
    """Re-fetch every published archive; require verified PHP CI or Node origin."""
    _coverage(catalog)
    evidence = []
    shasums: dict[str, str] = {}
    for artifact in catalog.manifest.artifacts:
        with tempfile.TemporaryDirectory(prefix="paddock-catalog-artifact-") as temporary:
            filename = urlsplit(artifact.url).path.rsplit("/", 1)[-1]
            archive = Path(temporary) / filename
            digest = _download(artifact.url, archive)
            if digest != artifact.sha256:
                raise PromotionError(f"artifact checksum mismatch for {artifact.url}: {digest}")
            if catalog.kind == "php":
                commit = _php_provenance(archive, digest)
                evidence.append(f"{filename} sha256={digest} CI commit={commit}")
            else:
                _node_provenance(artifact, digest, shasums)
                evidence.append(f"{filename} sha256={digest} official Node SHASUMS256.txt")
    return evidence


def prepare(kind: str, candidate: Path, current: Path | None, output: Path) -> list[str]:
    if output.exists() or output.is_symlink():
        raise PromotionError(f"refusing to replace output: {output}")
    source = _read_json(candidate)
    if set(source) != {"schema_version", "artifacts"} or source["schema_version"] != 1:
        raise PromotionError("candidate must be a packaged v1 artifact index")
    revision = 1
    previous = None
    if current is not None:
        previous = RuntimeCatalog.load(current, kind)
        revision = previous.revision + 1
    artifacts = source["artifacts"]
    if not isinstance(artifacts, list):
        raise PromotionError("candidate artifacts must be a list")
    _catalog(json.dumps({"schema_version": 1, "revision": revision,
                         "artifacts": artifacts}).encode(), kind)
    line = "php" if kind == "php" else "node"
    canonical = sorted(artifacts, key=lambda item: (
        tuple(int(part) for part in item[line].split(".")), item["architecture"]
    ))
    document = {"schema_version": 1, "revision": revision, "artifacts": canonical}
    payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
    catalog = _catalog(payload, kind)
    if previous is not None:
        old = {
            (item.minor if kind == "php" else item.major, item.architecture): item
            for item in previous.manifest.artifacts
        }
        for item in catalog.manifest.artifacts:
            line = item.minor if kind == "php" else item.major
            earlier = old.get((line, item.architecture))
            if earlier is None:
                continue
            old_patch = earlier.php if kind == "php" else earlier.node
            new_patch = item.php if kind == "php" else item.node
            if tuple(map(int, new_patch.split("."))) < tuple(map(int, old_patch.split("."))):
                raise PromotionError(f"{kind} {line} {item.architecture} regressed from {old_patch} to {new_patch}")
            if new_patch == old_patch and (item.sha256 != earlier.sha256 or item.url != earlier.url):
                raise PromotionError(f"{kind} {line} {item.architecture} changed an existing patch's URL or hash")
    evidence = validate_artifacts(catalog)
    atomic_write(output, payload, mode=0o644, parent_mode=None)
    return evidence


def verify(kind: str, catalog: Path, signature: Path, *, key: Path = SOURCE_KEY) -> RuntimeCatalog:
    payload = catalog.read_bytes()
    signed = _catalog(payload, kind)
    _coverage(signed)
    CatalogStore(Paths.from_environment(), key_path=key,
                 fingerprint=PRIMARY_FINGERPRINT)._verify(payload, signature.read_bytes())
    return signed


def _git(pages: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(pages), *args], text=True,
                            capture_output=True, check=False)
    if result.returncode:
        raise PromotionError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _publish_files(pages: Path, kind: str, catalog: bytes, signature: bytes,
                   revision: int) -> tuple[Path, Path, Path, Path]:
    root = pages / "runtime-catalogs"
    archive = root / "archive" / kind / f"{revision}.json"
    archived_sig = archive.with_suffix(".json.sig")
    stable = root / f"{kind}.json"
    stable_sig = stable.with_suffix(".json.sig")
    for directory in (root, root / "archive", archive.parent):
        if directory.is_symlink():
            raise PromotionError(f"publication directory is a symlink: {directory}")
    if archive.exists() or archive.is_symlink() or archived_sig.exists() or archived_sig.is_symlink():
        raise PromotionError(f"revision {revision} already has an archived {kind} catalog")
    if stable.is_symlink() or stable_sig.is_symlink():
        raise PromotionError("stable catalog paths must be regular files")
    if stable.exists() != stable_sig.exists():
        raise PromotionError("stable catalog and signature must both exist or both be absent")
    previous = RuntimeCatalog.load(stable, kind).revision if stable.exists() else 0
    if revision != previous + 1:
        raise PromotionError(f"{kind} revision must be {previous + 1}, got {revision}")
    atomic_write(archive, catalog, mode=0o644, parent_mode=None)
    atomic_write(archived_sig, signature, mode=0o644, parent_mode=None)
    return archive, archived_sig, stable, stable_sig


def publish(kind: str, catalog: Path, signature: Path, pages: Path,
            *, key: Path = SOURCE_KEY) -> None:
    signed = verify(kind, catalog, signature, key=key)
    payload, sig = catalog.read_bytes(), signature.read_bytes()
    pages = pages.resolve(strict=True)
    if _git(pages, "branch", "--show-current") != "gh-pages":
        raise PromotionError("publication checkout must be on gh-pages")
    if _git(pages, "status", "--porcelain"):
        raise PromotionError("publication checkout must be clean")
    remote = _git(pages, "remote", "get-url", "origin")
    if not re.fullmatch(r"(?:git@github\.com:|https://github\.com/)lukeska/paddock(?:\.git)?", remote):
        raise PromotionError("origin is not github.com/lukeska/paddock")
    _git(pages, "fetch", "origin", "gh-pages")
    if _git(pages, "rev-parse", "HEAD") != _git(pages, "rev-parse", "origin/gh-pages"):
        raise PromotionError("gh-pages checkout is not at the current origin tip")
    archive, archived_sig, stable, stable_sig = _publish_files(
        pages, kind, payload, sig, signed.revision
    )
    _git(pages, "add", "--", str(archive.relative_to(pages)), str(archived_sig.relative_to(pages)))
    _git(pages, "commit", "-m", f"Archive {kind} runtime catalog revision {signed.revision}")
    _git(pages, "push", "origin", "gh-pages")
    atomic_write(stable, payload, mode=0o644, parent_mode=None)
    atomic_write(stable_sig, sig, mode=0o644, parent_mode=None)
    _git(pages, "add", "--", str(stable.relative_to(pages)), str(stable_sig.relative_to(pages)))
    _git(pages, "commit", "-m", f"Promote {kind} runtime catalog revision {signed.revision}")
    _git(pages, "push", "origin", "gh-pages")
    url = CATALOG_URLS[kind]
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            with tempfile.TemporaryDirectory(prefix="paddock-pages-check-") as temporary:
                live_catalog = Path(temporary) / "catalog.json"
                live_sig = Path(temporary) / "catalog.json.sig"
                _download(url, live_catalog, limit=1024 * 1024)
                _download(url + ".sig", live_sig, limit=64 * 1024)
                if live_catalog.read_bytes() == payload and live_sig.read_bytes() == sig:
                    return
        except PromotionError:
            pass
        time.sleep(10)
    raise PromotionError("pushed catalog but Pages did not serve the exact signed bytes within 5 minutes")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "verify", "publish"):
        command = commands.add_parser(name)
        command.add_argument("--kind", choices=("php", "node"), required=True)
        if name == "prepare":
            command.add_argument("--candidate", type=Path, required=True)
            command.add_argument("--current", type=Path)
            command.add_argument("--output", type=Path, required=True)
        else:
            command.add_argument("--catalog", type=Path, required=True)
            command.add_argument("--signature", type=Path, required=True)
            command.add_argument("--key", type=Path, default=SOURCE_KEY)
        if name == "publish":
            command.add_argument("--pages-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            for line in prepare(args.kind, args.candidate, args.current, args.output):
                print(line)
            print(f"Prepared unsigned revision: {args.output} sha256={hashlib.sha256(args.output.read_bytes()).hexdigest()}")
        elif args.command == "verify":
            signed = verify(args.kind, args.catalog, args.signature, key=args.key)
            print(f"Verified {args.kind} catalog revision {signed.revision}")
        else:
            publish(args.kind, args.catalog, args.signature, args.pages_dir, key=args.key)
            print(f"Published {args.kind} catalog and verified Pages delivery")
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"catalog promotion: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
