"""Conservative updates to a project's user-owned ``.env`` file."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Mapping

from .atomic import atomic_write


ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
UNQUOTED_VALUE = re.compile(r"^[A-Za-z0-9_./:@+-]*$")


def format_value(value: str) -> str:
    """Encode one literal value using syntax understood by phpdotenv."""
    if UNQUOTED_VALUE.fullmatch(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{escaped}"'


def render(original: str, desired: Mapping[str, str]) -> str:
    """Return ``original`` with only the declared active assignments changed.

    Comments, blank lines, and keys Paddock does not own are retained. If a
    key occurs more than once, every active occurrence is made consistent so
    a later duplicate cannot silently override the declared value.
    """
    if not desired:
        return original

    newline = "\r\n" if "\r\n" in original else "\n"
    found: set[str] = set()
    rendered: list[str] = []
    for line in original.splitlines():
        match = ASSIGNMENT.match(line)
        key = match.group(1) if match else None
        if key in desired:
            rendered.append(f"{key}={format_value(desired[key])}")
            found.add(key)
        else:
            rendered.append(line)

    missing = [
        f"{key}={format_value(value)}"
        for key, value in desired.items()
        if key not in found
    ]
    if missing:
        if rendered and any(line for line in rendered):
            if rendered[-1] != "":
                rendered.append("")
        rendered.extend(missing)

    return newline.join(rendered).rstrip("\r\n") + newline


def reconcile(root: Path, desired: Mapping[str, str], *, dry_run: bool = False) -> bool:
    """Converge ``root/.env`` and return whether its content would change.

    A missing file starts from ``.env.example`` when available, which matches
    the normal Laravel bootstrap workflow. New files are private; updates
    preserve the existing file's mode and never chmod the project directory.
    """
    if not desired:
        return False
    path = root / ".env"
    source = path if path.is_file() else root / ".env.example"
    if path.is_symlink() or source.is_symlink():
        raise ValueError(f"refusing to update dotenv symbolic link: {source}")
    try:
        original = source.read_bytes().decode("utf-8") if source.is_file() else ""
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"cannot read {source}: {error}") from error
    updated = render(original, desired)
    changed = not path.is_file() or updated != original
    if changed and not dry_run:
        mode = (path.stat().st_mode & 0o777) if path.exists() else 0o600
        atomic_write(path, updated.encode("utf-8"), mode=mode, parent_mode=None)
    return changed
