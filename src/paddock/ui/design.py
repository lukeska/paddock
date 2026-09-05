"""GTK CSS generation with no runtime dependency on PyGObject."""


def design_css() -> str:
    """Static structure; all colors remain supplied by the live theme adapter."""
    return """
.paddock-shell { font-family: monospace; }
.paddock-page { padding: 24px; }
.paddock-shell .navigation-sidebar row:selected { border-radius: 0; }
.paddock-shell button,
.paddock-shell button.suggested-action,
.paddock-shell button.destructive-action,
.paddock-dialog button,
.paddock-dialog button.suggested-action,
.paddock-dialog button.destructive-action {
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
.paddock-shell button.suggested-action,
.paddock-dialog button.suggested-action { color: var(--accent-color); }
.paddock-shell button.destructive-action,
.paddock-dialog button.destructive-action { color: var(--error-bg-color); }
.paddock-shell button.paddock-text-link {
  min-height: 0;
  min-width: 0;
  padding: 0;
  border: 0;
  background-color: transparent;
  background-image: none;
  box-shadow: none;
  color: var(--accent-color);
  text-decoration-line: underline;
}
.paddock-hero,
.paddock-card {
  background-color: var(--card-bg-color);
  border: 1px solid var(--border-color);
}
.paddock-hero { padding: 18px; border-radius: 8px; }
.paddock-card { padding: 2px; border-radius: 0; }
.paddock-card > row { background-color: transparent; }
.paddock-shell popover > contents,
.paddock-shell popover list,
.paddock-shell popover list row {
  background-color: var(--popover-bg-color);
}
.paddock-shell popover > contents {
  border: 1px solid var(--border-color);
}
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
.paddock-sites-rail {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-color);
  background-color: var(--headerbar-bg-color);
}
"""
