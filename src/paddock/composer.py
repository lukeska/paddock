from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Callable
from urllib.request import urlopen


class ComposerInstallError(RuntimeError):
    pass


Opener = Callable[..., object]


def install_composer(data: Path, catalog: Path, opener: Opener = urlopen) -> str | None:
    """Install the catalog-pinned Composer PHAR, returning its version if changed."""
    try:
        raw = json.loads(catalog.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ComposerInstallError(f"cannot read Composer catalog {catalog}: {error}") from error
    expected = {"schema_version", "version", "url", "sha256"}
    if not isinstance(raw, dict) or set(raw) != expected or raw["schema_version"] != 1:
        raise ComposerInstallError("invalid Composer artifact catalog")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", str(raw["version"])):
        raise ComposerInstallError("invalid Composer version")
    if not isinstance(raw["url"], str) or not raw["url"].startswith("https://"):
        raise ComposerInstallError("Composer artifact URL must use HTTPS")
    if not isinstance(raw["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", raw["sha256"]):
        raise ComposerInstallError("invalid Composer artifact sha256")

    destination = data / "composer" / "composer.phar"
    if destination.is_file() and _sha256(destination) == raw["sha256"]:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".composer-", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output, opener(raw["url"]) as response:
            shutil.copyfileobj(response, output)
            output.flush()
            os.fsync(output.fileno())
        actual = _sha256(temporary)
        if actual != raw["sha256"]:
            raise ComposerInstallError(
                f"Composer checksum mismatch: expected {raw['sha256']}, got {actual}"
            )
        temporary.chmod(0o600)
        os.replace(temporary, destination)
    except OSError as error:
        raise ComposerInstallError(f"cannot install Composer: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)
    return raw["version"]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
