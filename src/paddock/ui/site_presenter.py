"""Pure site presentation helpers shared by GTK and its tests."""

from __future__ import annotations


def terminal_command(root: str) -> list[str]:
    """Use Omarchy's configured terminal without involving a shell."""
    return ["xdg-terminal-exec", f"--dir={root}"]


def zed_command(root: str) -> list[str]:
    """Open one project in Zed without involving a shell."""
    return ["zeditor", root]


def site_name_matches(name: str, query: str) -> bool:
    return query.strip().casefold() in name.casefold()


def site_worker_summary(site) -> str:
    statuses = []
    if site.queue_available or site.queue_configured:
        if not site.queue_configured:
            statuses.append("Queue available")
        elif site.queue_state == "active":
            statuses.append("Queue running")
        elif site.queue_state == "failed":
            statuses.append("Queue failed")
        else:
            statuses.append("Queue stopped")
    if site.reverb_available or site.reverb_configured:
        if not site.reverb_configured:
            statuses.append("Reverb available")
        elif site.reverb_state == "active":
            statuses.append("Reverb running")
        elif site.reverb_state == "failed":
            statuses.append("Reverb failed")
        else:
            statuses.append("Reverb stopped")
    if not statuses:
        return "No workers"
    return " · ".join(statuses)


def error_clipboard_text(heading: str, detail: str) -> str:
    """Keep copied errors complete and useful when pasted into a report."""
    return f"{heading}\n\n{detail}\n"
