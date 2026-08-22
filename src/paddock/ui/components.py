"""GTK-native components modeled on Omarchy Shell's visual hierarchy."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


def design_css() -> str:
    """Static structure; all colors remain supplied by the live theme adapter."""
    return """
.paddock-shell { font-family: monospace; }
.paddock-page { padding: 24px; }
.paddock-hero,
.paddock-card {
  background-color: var(--card-bg-color);
  border: 1px solid var(--border-color);
  border-radius: 8px;
}
.paddock-hero { padding: 18px; }
.paddock-card { padding: 2px; }
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
