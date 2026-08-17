from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


class PathConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Paths:
    config: Path
    data: Path
    state: Path
    cache: Path
    runtime: Path | None

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        require_runtime: bool = False,
    ) -> "Paths":
        env = os.environ if environment is None else environment
        home_value = env.get("HOME")
        if not home_value:
            raise PathConfigurationError("HOME is required")
        home = Path(home_value).expanduser()
        runtime_value = env.get("XDG_RUNTIME_DIR")
        if require_runtime and not runtime_value:
            raise PathConfigurationError("XDG_RUNTIME_DIR is required")
        return cls(
            config=Path(env.get("XDG_CONFIG_HOME", home / ".config")) / "paddock",
            data=Path(env.get("XDG_DATA_HOME", home / ".local/share")) / "paddock",
            state=Path(env.get("XDG_STATE_HOME", home / ".local/state")) / "paddock",
            cache=Path(env.get("XDG_CACHE_HOME", home / ".cache")) / "paddock",
            runtime=Path(runtime_value) / "paddock" if runtime_value else None,
        )

    def initialize(self, *, include_runtime: bool = False) -> None:
        roots = [self.config, self.data, self.state, self.cache]
        if include_runtime:
            if self.runtime is None:
                raise PathConfigurationError("XDG_RUNTIME_DIR is required")
            roots.append(self.runtime)
        for root in roots:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            root.chmod(0o700)

