"""GTK attachment for the pure Omarchy palette adapter."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Mapping

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .theme import OmarchyPalette, ThemeError, load_palette, palette_css
from .components import design_css


class OmarchyThemeAdapter:
    """Apply and live-reload Omarchy's active palette without modifying it."""

    def __init__(
        self,
        display,
        environment: Mapping[str, str] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        env = os.environ if environment is None else environment
        home = Path(env.get("HOME", "")).expanduser()
        self.current_directory = home / ".local/state/omarchy/current"
        self.palette_path = self.current_directory / "theme/colors.toml"
        self.display = display
        self.on_error = on_error
        self.provider = Gtk.CssProvider()
        self.monitor: Gio.FileMonitor | None = None
        self.reload_source = 0
        self.last_palette: OmarchyPalette | None = None
        self.style_manager = Adw.StyleManager.get_for_display(display)

    def start(self) -> None:
        self.apply()
        try:
            file = Gio.File.new_for_path(str(self.current_directory))
            self.monitor = file.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            self.monitor.connect("changed", self._changed)
        except GLib.Error as error:
            self._report(f"Cannot monitor Omarchy themes: {error.message}")

    def apply(self) -> bool:
        try:
            palette = load_palette(self.palette_path)
        except ThemeError as error:
            if self.last_palette is None:
                self._use_system_style()
            self._report(str(error))
            return False

        self.provider.load_from_string(palette_css(palette) + design_css())
        Gtk.StyleContext.add_provider_for_display(
            self.display,
            self.provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        scheme = (
            Adw.ColorScheme.FORCE_DARK
            if palette.mode == "dark"
            else Adw.ColorScheme.FORCE_LIGHT
        )
        self.style_manager.set_color_scheme(scheme)
        self.last_palette = palette
        return True

    def close(self) -> None:
        if self.reload_source:
            GLib.source_remove(self.reload_source)
            self.reload_source = 0
        if self.monitor is not None:
            self.monitor.cancel()
            self.monitor = None
        Gtk.StyleContext.remove_provider_for_display(self.display, self.provider)
        self._use_system_style()

    def _changed(self, *_args) -> None:
        if self.reload_source:
            GLib.source_remove(self.reload_source)
        self.reload_source = GLib.timeout_add(120, self._reload)

    def _reload(self) -> bool:
        self.reload_source = 0
        self.apply()
        return GLib.SOURCE_REMOVE

    def _use_system_style(self) -> None:
        Gtk.StyleContext.remove_provider_for_display(self.display, self.provider)
        self.style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)

    def _report(self, message: str) -> None:
        if self.on_error is not None:
            self.on_error(message)
