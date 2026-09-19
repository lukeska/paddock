"""Read and validate Omarchy's normalized palette for terminal clients."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
import re
import tomllib


HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


class ThemeError(ValueError):
    pass


@dataclass(frozen=True)
class OmarchyPalette:
    mode: str
    accent: str | None = None
    selection: str | None = None
    muted: str | None = None
    background: str | None = None
    dark_background: str | None = None
    darker_background: str | None = None
    lighter_background: str | None = None
    foreground: str | None = None
    dark_foreground: str | None = None
    light_foreground: str | None = None
    bright_foreground: str | None = None
    red: str | None = None
    yellow: str | None = None
    orange: str | None = None
    green: str | None = None
    cyan: str | None = None
    blue: str | None = None
    magenta: str | None = None
    brown: str | None = None


def load_palette(path: Path) -> OmarchyPalette:
    """Read one palette strictly enough to expose only normalized colors."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ThemeError(f"cannot read Omarchy palette at {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ThemeError("Omarchy palette must be a TOML table")
    mode = raw.get("mode")
    if mode not in {"light", "dark"}:
        raise ThemeError("Omarchy palette mode must be light or dark")

    values: dict[str, str | None] = {"mode": mode}
    for field in fields(OmarchyPalette):
        if field.name == "mode":
            continue
        value = raw.get(field.name)
        if value is None:
            values[field.name] = None
        elif isinstance(value, str) and HEX_COLOR.fullmatch(value):
            values[field.name] = value.lower()
        else:
            raise ThemeError(f"Omarchy palette {field.name} must be #RRGGBB")
    return OmarchyPalette(**values)

