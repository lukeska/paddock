"""Helpers for tests that assert on the projected nginx tree.

One Caddyfile became a tree, so a test that used to read a single file now
either wants one site's block or the whole promoted generation. Both readings
go through `current`, which is what the running unit reads, so a test can
never accidentally assert about a generation that was staged and rejected.
"""

from __future__ import annotations


def promoted(projector) -> str:
    """Every configuration file in the promoted generation, concatenated."""
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(projector.current.rglob("*.conf"))
    )


def site_configuration(projector, name: str) -> str:
    """One site's generated server blocks."""
    return (projector.current / "sites" / f"{name}.conf").read_text(encoding="utf-8")
