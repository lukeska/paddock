"""GTK-native components modeled on Omarchy Shell's visual hierarchy."""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gtk  # noqa: E402


def design_css() -> str:
    """Static structure; all colors remain supplied by the live theme adapter."""
    return """
.paddock-shell { font-family: monospace; }
.paddock-page { padding: 24px; }
.paddock-shell .navigation-sidebar row:selected { border-radius: 0; }
.paddock-shell button,
.paddock-shell button.suggested-action,
.paddock-shell button.destructive-action {
  min-height: 22px;
  min-width: 22px;
  padding: 3px 8px;
  border: 1px solid var(--border-color);
  border-radius: 0;
  background-color: transparent;
  background-image: none;
  box-shadow: none;
  font-size: 0.82em;
}
.paddock-shell button.suggested-action { color: var(--accent-color); }
.paddock-shell button.destructive-action { color: var(--error-bg-color); }
.paddock-hero,
.paddock-card {
  background-color: var(--card-bg-color);
  border: 1px solid var(--border-color);
}
.paddock-hero { padding: 18px; border-radius: 8px; }
.paddock-card { padding: 2px; border-radius: 0; }
.paddock-card row { background-color: transparent; }
.paddock-hero-icon { color: var(--accent-color); }
.paddock-hero-title { font-size: 1.3em; font-weight: bold; }
.paddock-hero-meta,
.paddock-section-title {
  opacity: 0.7;
  font-size: 0.82em;
  font-weight: bold;
  letter-spacing: 1px;
}
.paddock-section-title { margin: 4px 2px; }
.paddock-danger-title { color: var(--error-bg-color); opacity: 1; }
.paddock-status-pill {
  padding: 3px 8px;
  border: 1px solid var(--border-color);
  border-radius: 999px;
  font-weight: bold;
}
.paddock-hero-actions { margin-left: 12px; }
.paddock-service-row {
  min-height: 32px;
  padding-top: 0;
  padding-bottom: 0;
  font-size: 0.9em;
}
.paddock-env-block {
  padding: 12px;
  font-family: monospace;
  font-size: 0.9em;
}
.paddock-led { font-size: 1.35em; }
.paddock-led-active { color: var(--success-bg-color); }
.paddock-led-inactive { color: var(--error-bg-color); }
.paddock-dashboard-heading { font-size: 1.45em; font-weight: bold; }
"""


class PaddockHero(Gtk.Box):
    """Prominent service identity, state, and primary controls."""

    def __init__(self, icon_name: str, title: str, meta: str):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.add_css_class("paddock-hero")

        self.icon = Gtk.Image.new_from_icon_name(icon_name)
        self.icon.set_pixel_size(38)
        self.icon.add_css_class("paddock-hero-icon")
        self.append(self.icon)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        labels.set_hexpand(True)
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.title = Gtk.Label(label=title, xalign=0)
        self.title.set_hexpand(True)
        self.title.set_ellipsize(3)
        self.title.add_css_class("paddock-hero-title")
        self.status = Gtk.Label(label="Loading")
        self.status.add_css_class("paddock-status-pill")
        heading.append(self.title)
        heading.append(self.status)
        labels.append(heading)
        self.meta = Gtk.Label(label=meta.upper(), xalign=0)
        self.meta.add_css_class("paddock-hero-meta")
        labels.append(self.meta)
        self.description = Gtk.Label(xalign=0, wrap=True)
        self.description.add_css_class("dim-label")
        labels.append(self.description)
        self.append(labels)

        self.actions = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            valign=Gtk.Align.CENTER,
        )
        self.actions.add_css_class("paddock-hero-actions")
        self.append(self.actions)

    def set_presentation(
        self, status: str, description: str, icon_name: str, tone: str | None
    ) -> None:
        self.description.set_label(description)
        self.icon.set_from_icon_name(icon_name)
        self.status.set_label(status)
        for name in ("success", "warning", "error"):
            self.status.remove_css_class(name)
        if tone:
            self.status.add_css_class(tone)


class PaddockSection(Gtk.Box):
    """Small-caps section label followed by one compact bordered card."""

    def __init__(self, title: str, *, danger: bool = False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.title = Gtk.Label(label=title.upper(), xalign=0)
        self.title.add_css_class("paddock-section-title")
        if danger:
            self.title.add_css_class("paddock-danger-title")
        self.append(self.title)
        self.rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.rows.add_css_class("paddock-card")
        self.append(self.rows)

    def add(self, row: Gtk.Widget) -> None:
        self.rows.append(row)

    def clear(self) -> None:
        while child := self.rows.get_first_child():
            self.rows.remove(child)


class PaddockServiceRow(Adw.ActionRow):
    """Compact service row whose red/green LED carries the state."""

    def __init__(
        self, title: str, detail: str, state: str, *, show_detail: bool = False
    ):
        super().__init__(title=title, subtitle=detail if show_detail else "")
        self.add_css_class("paddock-service-row")
        self.led = Gtk.Label(label="●")
        self.led.add_css_class("paddock-led")
        self.add_prefix(self.led)
        self.set_state(state)

    def set_state(self, state: str) -> None:
        for css_class in ("paddock-led-active", "paddock-led-inactive"):
            self.led.remove_css_class(css_class)
        active = state == "active"
        self.led.add_css_class(
            "paddock-led-active" if active else "paddock-led-inactive"
        )
        self.led.set_tooltip_text("Active" if active else "Inactive")
