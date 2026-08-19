from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


class PathConfigurationError(ValueError):
    pass


# Sockets and PIDs live in a systemd-owned runtime directory rather than
# `$XDG_RUNTIME_DIR`. The PHP units are system units that must survive boot
# before any login and stay up after logout with linger disabled, so their
# runtime directory cannot belong to a login session. `RuntimeDirectory=` in
# `paddock-php@.service` creates and removes `/run/paddock/php/<minor>`; the
# value here only has to agree with that unit. See ADR 0006.
SYSTEM_RUNTIME_ROOT = Path("/run/paddock")


@dataclass(frozen=True)
class Paths:
    config: Path
    data: Path
    state: Path
    cache: Path
    runtime: Path

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        runtime_root: Path = SYSTEM_RUNTIME_ROOT,
    ) -> "Paths":
        env = os.environ if environment is None else environment
        home_value = env.get("HOME")
        if not home_value:
            raise PathConfigurationError("HOME is required")
        home = Path(home_value).expanduser()
        return cls(
            config=Path(env.get("XDG_CONFIG_HOME", home / ".config")) / "paddock",
            data=Path(env.get("XDG_DATA_HOME", home / ".local/share")) / "paddock",
            state=Path(env.get("XDG_STATE_HOME", home / ".local/state")) / "paddock",
            cache=Path(env.get("XDG_CACHE_HOME", home / ".cache")) / "paddock",
            runtime=runtime_root,
        )

    def initialize(self) -> None:
        # `runtime` is deliberately absent: systemd owns it and Paddock runs
        # unprivileged, so a fresh boot must never require it to exist here.
        for root in (self.config, self.data, self.state, self.cache):
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            root.chmod(0o700)

