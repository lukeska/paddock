"""Safe translation from Omarchy's normalized palette to Libadwaita CSS."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
import re
import tomllib
from typing import Mapping


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
    """Read one palette strictly enough that it can never inject GTK CSS."""
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


def palette_css(palette: OmarchyPalette) -> str:
    """Generate only normalized literals and known Libadwaita variable names."""
    variables: dict[str, str | None] = {
        "--accent-bg-color": palette.accent,
        "--accent-color": palette.accent,
        "--accent-fg-color": contrast_foreground(palette.accent),
        "--window-bg-color": palette.background,
        "--window-fg-color": palette.foreground,
        "--view-bg-color": palette.darker_background or palette.background,
        "--view-fg-color": palette.foreground,
        "--headerbar-bg-color": palette.dark_background or palette.background,
        "--headerbar-fg-color": palette.foreground,
        "--sidebar-bg-color": palette.dark_background or palette.background,
        "--sidebar-fg-color": palette.foreground,
        "--card-bg-color": palette.lighter_background,
        "--card-fg-color": palette.foreground,
        "--dialog-bg-color": palette.background,
        "--dialog-fg-color": palette.foreground,
        "--popover-bg-color": palette.lighter_background or palette.background,
        "--popover-fg-color": palette.foreground,
        "--border-color": palette.muted,
        "--success-bg-color": palette.green,
        "--success-fg-color": contrast_foreground(palette.green),
        "--warning-bg-color": palette.orange or palette.yellow,
        "--warning-fg-color": contrast_foreground(palette.orange or palette.yellow),
        "--error-bg-color": palette.red,
        "--error-fg-color": contrast_foreground(palette.red),
    }
    declarations = "\n".join(
        f"  {name}: {value};" for name, value in variables.items() if value is not None
    )
    return f":root {{\n{declarations}\n}}\n"


def contrast_foreground(background: str | None) -> str | None:
    if background is None:
        return None
    red, green, blue = (
        int(background[index:index + 2], 16) / 255 for index in (1, 3, 5)
    )

    def linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)
    white_contrast = 1.05 / (luminance + 0.05)
    black_contrast = (luminance + 0.05) / 0.05
    return "#ffffff" if white_contrast >= black_contrast else "#000000"

