#!/usr/bin/env python3
"""Reject an application release whose tag and package metadata disagree."""

from __future__ import annotations

import re
from pathlib import Path
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def value(pattern: str, text: str, label: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise ValueError(f"cannot read {label}")
    return match.group(1)


def validate(tag: str) -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    if tag != f"v{version}":
        raise ValueError(f"tag {tag!r} does not match project version v{version}")

    package = (ROOT / "packaging/arch/PKGBUILD").read_text(encoding="utf-8")
    if value(r"^pkgver=([^\s]+)$", package, "pkgver") != version:
        raise ValueError("PKGBUILD pkgver does not match the project version")
    if value(r"^pkgrel=([^\s]+)$", package, "pkgrel") != "1":
        raise ValueError("release PKGBUILD pkgrel must be 1")

    module = (ROOT / "src/paddock/__init__.py").read_text(encoding="utf-8")
    if value(r'^__version__\s*=\s*"([^"]+)"$', module, "module version") != version:
        raise ValueError("Python module version does not match the project version")

    expected_source = (
        'source=("$pkgname-$pkgver.tar.gz::'
        '$url/releases/download/v$pkgver/$pkgname-$pkgver.tar.gz")'
    )
    if expected_source not in package:
        raise ValueError("PKGBUILD source is not the immutable GitHub release asset")
    checksum = value(r"^sha256sums=\('([0-9a-f]{64})'\)$", package, "source checksum")
    if checksum == "0" * 64:
        raise ValueError("PKGBUILD source checksum is still a placeholder")
    return version


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} TAG", file=sys.stderr)
        return 2
    try:
        version = validate(sys.argv[1])
    except (KeyError, OSError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"release validation failed: {error}", file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
