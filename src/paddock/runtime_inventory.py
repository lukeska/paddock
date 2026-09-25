"""Recover patch versions from Paddock-managed legacy installations only."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess


PHP_RELEASE = re.compile(r"php-([0-9]+\.[0-9]+\.[0-9]+)-([0-9a-f]{12})")
NODE_RELEASE = re.compile(r"node-([0-9]+\.[0-9]+\.[0-9]+)-([0-9a-f]{12})")


def installed_patch(path: Path, digest: str, releases: Path, kind: str, line: str) -> str | None:
    """Probe an old record only when its path has Paddock's managed shape.

    Arbitrary registered paths are not executed during a read-only snapshot.
    A failed or inconsistent probe is recorded as unknown by the registry.
    """
    try:
        executable = path.resolve(strict=True)
        root = releases.resolve(strict=True)
    except OSError:
        return None
    expected_name = "php" if kind == "php" else "node"
    if executable.name != expected_name or executable.parent.name != "bin":
        return None
    release_dir = executable.parent.parent
    if release_dir.parent != root:
        return None
    match = (PHP_RELEASE if kind == "php" else NODE_RELEASE).fullmatch(release_dir.name)
    if match is None or match.group(2) != digest[:12]:
        return None
    patch = match.group(1)
    if (".".join(patch.split(".")[:2]) if kind == "php" else patch.split(".")[0]) != line:
        return None
    command = ([str(executable), "-n", "-r", "echo PHP_VERSION;"]
               if kind == "php" else [str(executable), "--version"])
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    reported = result.stdout.strip().removeprefix("v" if kind == "node" else "")
    return patch if result.returncode == 0 and reported == patch else None
