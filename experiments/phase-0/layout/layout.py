#!/usr/bin/env python3

from __future__ import annotations

import argparse
import errno
import os
from pathlib import Path
import tempfile


def roots() -> dict[str, Path]:
    home = Path.home()
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise SystemExit("paddock: XDG_RUNTIME_DIR is required")
    return {
        "config": Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "paddock",
        "data": Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "paddock",
        "state": Path(os.environ.get("XDG_STATE_HOME", home / ".local/state")) / "paddock",
        "runtime": Path(runtime) / "paddock",
        "cache": Path(os.environ.get("XDG_CACHE_HOME", home / ".cache")) / "paddock",
    }


def initialize() -> None:
    os.umask(0o077)
    for path in roots().values():
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)


def atomic_write(path: Path, value: bytes, simulate_enospc: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        if simulate_enospc:
            raise OSError(errno.ENOSPC, "injected disk-space failure")
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("paths")
    subparsers.add_parser("init")
    writer = subparsers.add_parser("atomic-write")
    writer.add_argument("path", type=Path)
    writer.add_argument("value")
    writer.add_argument("--simulate-enospc", action="store_true")
    arguments = parser.parse_args()
    if arguments.command == "paths":
        for name, path in roots().items():
            print(f"{name}={path}")
    elif arguments.command == "init":
        initialize()
    else:
        atomic_write(arguments.path, arguments.value.encode(), arguments.simulate_enospc)


if __name__ == "__main__":
    main()

