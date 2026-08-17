#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re


PATTERN = re.compile(r"paddock-php-(\d+\.\d+\.\d+)-linux-(x86_64|aarch64)\.tar\.gz$")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default=os.environ.get("ARTIFACT_BASE_URL"))
    args = parser.parse_args()
    artifacts = []
    for path in sorted(args.dist.glob("paddock-php-*-linux-*.tar.gz")):
        match = PATTERN.fullmatch(path.name)
        if not match:
            continue
        version, architecture = match.groups()
        url = f"{args.base_url.rstrip('/')}/{path.name}" if args.base_url else path.resolve().as_uri()
        artifacts.append(
            {
                "php": version,
                "minor": ".".join(version.split(".")[:2]),
                "architecture": architecture,
                "url": url,
                "sha256": sha256(path),
            }
        )
    args.output.write_text(
        json.dumps({"schema_version": 1, "artifacts": artifacts}, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
