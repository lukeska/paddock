#!/usr/bin/env python3
"""Prepare and exercise disposable PHP/Node patch-upgrade fixtures on a VM."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile


PHP_OLD = "8.5.8"
NODE_OLD = "24.19.0"
NODE_SHA256 = "14b342e71204f811bde6153be8e04b62aef63c236fef92b55f9c83154b409647"
PHP_ARCHIVE = f"paddock-php-{PHP_OLD}-linux-x86_64.tar.gz"


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def run(*arguments: str) -> str:
    result = subprocess.run(arguments, text=True, capture_output=True, check=False)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise ValueError(f"{' '.join(arguments)}: {detail}")
    return result.stdout


def document(kind: str, archive: Path | None = None) -> bytes:
    if kind == "php":
        if archive is None:
            raise ValueError("PHP archive is required")
        artifacts = [{
            "php": PHP_OLD, "minor": "8.5", "architecture": "x86_64",
            "url": archive.resolve(strict=True).as_uri(), "sha256": digest(archive),
        }]
    elif kind == "node":
        artifacts = [{
            "node": NODE_OLD, "major": "24", "architecture": "x86_64",
            "url": f"https://nodejs.org/download/release/v{NODE_OLD}/node-v{NODE_OLD}-linux-x64.tar.xz",
            "sha256": NODE_SHA256,
        }]
    else:
        raise ValueError(f"unknown runtime: {kind}")
    return (json.dumps({"schema_version": 1, "artifacts": artifacts}, indent=2) + "\n").encode()


def validate_php_archive(archive: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="paddock-old-php-") as temporary:
        with tarfile.open(archive, "r:gz") as source:
            members = source.getmembers()
            if any(member.issym() or member.islnk() or member.isdev() for member in members):
                raise ValueError("PHP archive contains an unsafe member")
            binary = next((member for member in members if member.name == "runtime/bin/php"), None)
            fpm = next((member for member in members if member.name == "runtime/bin/php-fpm"), None)
            if binary is None or fpm is None:
                raise ValueError("PHP archive is missing CLI or FPM binary")
            source.extract(binary, temporary, filter="data")
            source.extract(fpm, temporary, filter="data")
        php = Path(temporary) / "runtime/bin/php"
        output = run(str(php), "-n", "-r", 'echo PHP_VERSION, " ", function_exists("mb_split") ? "yes" : "no";')
        if output != f"{PHP_OLD} yes":
            raise ValueError(f"older PHP build failed version/mb_split check: {output!r}")
        if PHP_OLD not in run(str(Path(temporary) / "runtime/bin/php-fpm"), "-n", "-v"):
            raise ValueError("older PHP-FPM version does not match")


def prepare(archive: Path, output: Path) -> None:
    if not archive.is_file():
        raise ValueError(f"PHP archive is missing: {archive}; run build-old-php.sh")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output directory is not empty: {output}")
    validate_php_archive(archive)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / PHP_ARCHIVE
    shutil.copyfile(archive, destination)
    shutil.copyfile(Path(__file__), output / "fixture.py")
    print(f"Prepared {output} with PHP {PHP_OLD} sha256={digest(destination)}")
    print("Copy this directory to the VM, then run fixture.py stage before setup or seed after setup")


def config_directory() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "paddock"


def fixture_payloads(directory: Path) -> tuple[bytes, bytes]:
    archive = directory / PHP_ARCHIVE
    validate_php_archive(archive)
    return document("php", archive), document("node")


def catalog_overrides() -> tuple[Path, Path]:
    config = config_directory()
    return config / "artifacts.json", config / "node-artifacts.json"


def stage(directory: Path) -> None:
    if os.geteuid() == 0:
        raise ValueError("run stage as the Paddock desktop user, not root")
    php, node = fixture_payloads(directory)
    paths = catalog_overrides()
    for path, payload in zip(paths, (php, node)):
        if path.exists() or path.is_symlink():
            raise ValueError(f"refusing to replace existing local catalog: {path}")
    paths[0].parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        for path, payload in zip(paths, (php, node)):
            with path.open("xb") as output:
                output.write(payload)
    except OSError:
        for path, payload in zip(paths, (php, node)):
            if path.is_file() and path.read_bytes() == payload:
                path.unlink()
        raise
    print("Test catalogs staged. Run paddock setup --yes, then fixture.py unstage.")


def unstage(directory: Path) -> None:
    php, node = fixture_payloads(directory)
    paths = catalog_overrides()
    for path, payload in zip(paths, (php, node)):
        if not path.is_file() or path.is_symlink() or path.read_bytes() != payload:
            raise ValueError(f"refusing to remove non-fixture catalog: {path}")
    for path in paths:
        path.unlink()
    print("Test catalogs removed; signed catalogs can now take precedence.")


def installed(kind: str, key: str) -> str | None:
    output = run("paddock", kind, "list")
    for line in output.splitlines():
        columns = line.split("\t")
        if len(columns) >= 2 and columns[0] == key:
            return columns[1]
    return None


def install_old(kind: str, key: str, expected: str, payload: bytes, filename: str) -> None:
    current = installed(kind, key)
    if current == expected:
        print(f"{kind} {expected} already installed")
        return
    if current is not None:
        raise ValueError(f"{kind} {key} already has {current}; use a fresh VM or explicitly remove it first")
    override = config_directory() / filename
    if override.exists() or override.is_symlink():
        raise ValueError(f"refusing to replace existing local catalog: {override}")
    with tempfile.TemporaryDirectory(prefix="paddock-upgrade-fixture-") as temporary:
        source = Path(temporary) / filename
        source.write_bytes(payload)
        try:
            print(run("paddock", kind, "catalog", str(source)).strip())
            print(run("paddock", kind, "install", key).strip())
        finally:
            if override.is_file() and override.read_bytes() == payload:
                override.unlink()
            elif override.exists() or override.is_symlink():
                raise ValueError(f"local catalog changed during fixture install; inspect {override}")
    if installed(kind, key) != expected:
        raise ValueError(f"{kind} {key} did not report installed patch {expected}")


def seed(directory: Path) -> None:
    if os.geteuid() == 0:
        raise ValueError("run seed as the Paddock desktop user, not root")
    if os.uname().machine != "x86_64":
        raise ValueError("this fixture targets x86_64 only")
    php, node = fixture_payloads(directory)
    for kind, key, filename in (("php", "8.5", "artifacts.json"), ("node", "24", "node-artifacts.json")):
        current = installed(kind, key)
        expected = PHP_OLD if kind == "php" else NODE_OLD
        if current not in (None, expected):
            raise ValueError(f"{kind} {key} already has {current}; use a fresh VM or explicitly remove it first")
        override = config_directory() / filename
        if override.exists() or override.is_symlink():
            raise ValueError(f"refusing to replace existing local catalog: {override}")
    install_old("php", "8.5", PHP_OLD, php, "artifacts.json")
    install_old("node", "24", NODE_OLD, node, "node-artifacts.json")
    print("Older patches installed. Local overrides removed; refresh signed catalogs next.")


def check(expected_php: str, expected_node: str) -> None:
    for kind, key, expected in (("php", "8.5", expected_php), ("node", "24", expected_node)):
        actual = installed(kind, key)
        if actual != expected:
            raise ValueError(f"{kind} {key}: expected {expected}, found {actual or 'not installed'}")
    for filename in ("artifacts.json", "node-artifacts.json"):
        override = config_directory() / filename
        if override.exists() or override.is_symlink():
            raise ValueError(f"local override still present: {override}")
    print(f"PHP {expected_php} and Node {expected_node} installed; no local catalog overrides")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("prepare", help="make a directory to copy to the VM")
    make.add_argument("--php-archive", type=Path, required=True)
    make.add_argument("--output", type=Path, required=True)
    commands.add_parser("seed", help="install older patches on the VM")
    commands.add_parser("stage", help="select older patches before first paddock setup")
    commands.add_parser("unstage", help="remove only the staged test catalogs")
    verify = commands.add_parser("check", help="check fixture versions and override cleanup")
    verify.add_argument("--php", required=True)
    verify.add_argument("--node", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(args.php_archive, args.output)
        elif args.command == "seed":
            seed(Path(__file__).resolve().parent)
        elif args.command == "stage":
            stage(Path(__file__).resolve().parent)
        elif args.command == "unstage":
            unstage(Path(__file__).resolve().parent)
        else:
            check(args.php, args.node)
    except (OSError, ValueError, tarfile.TarError) as error:
        print(f"runtime upgrade fixture: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
