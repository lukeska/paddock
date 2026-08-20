from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import tempfile
from typing import BinaryIO, Iterator


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def atomic_write(
    path: Path, payload: bytes, *, mode: int = 0o600, parent_mode: int | None = 0o700
) -> None:
    """Replace `path` with `payload`, surviving a crash mid-write.

    `parent_mode=None` leaves the parent directory's permissions alone, for the
    few files Paddock writes into a directory it does not own, such as a unit
    in the user's own `~/.config/systemd/user`. Clamping a shared directory to
    `0700` would be an unrequested change to someone else's files.
    """
    if parent_mode is None:
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True, mode=parent_mode)
        path.parent.chmod(parent_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            _write_and_sync(stream, payload)
        os.replace(temporary, path)
        os.chmod(path, mode)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _write_and_sync(stream: BinaryIO, payload: bytes) -> None:
    stream.write(payload)
    stream.flush()
    os.fsync(stream.fileno())

