"""Pure presentation rules for Redis snapshots."""

from __future__ import annotations

from dataclasses import dataclass

from paddock.application import RedisSnapshot


@dataclass(frozen=True)
class RedisPresentation:
    view: str
    title: str
    description: str
    icon_name: str
    tone: str | None
    can_start: bool
    can_stop: bool
    can_restart: bool
    transitioning: bool


def present_redis(snapshot: RedisSnapshot) -> RedisPresentation:
    if not snapshot.configured:
        if snapshot.active_state == "unknown":
            return RedisPresentation(
                "unknown",
                "Redis status unavailable",
                "Paddock could not read the Redis configuration. Try refreshing or run Doctor.",
                "dialog-warning-symbolic",
                "warning",
                False,
                False,
                False,
                False,
            )
        return RedisPresentation(
            "unconfigured",
            "Redis is not configured",
            "Add Redis to make a shared, loopback-only cache available to local projects.",
            "network-server-symbolic",
            None,
            False,
            False,
            False,
            False,
        )

    state = snapshot.active_state
    if state == "active":
        return RedisPresentation(
            "configured", "Running", f"Redis is ready at {snapshot.address}.",
            "emblem-ok-symbolic", "success", False, True, True, False,
        )
    if state in {"activating", "reloading"}:
        return RedisPresentation(
            "configured", "Starting…", "Redis is waiting for its readiness check.",
            "content-loading-symbolic", "warning", False, False, False, True,
        )
    if state == "deactivating":
        return RedisPresentation(
            "configured", "Stopping…", "Redis is shutting down.",
            "content-loading-symbolic", "warning", False, False, False, True,
        )
    if state == "failed":
        return RedisPresentation(
            "configured",
            "Failed",
            "Redis did not start successfully. Inspect its logs before retrying.",
            "dialog-error-symbolic", "error", True, False, True, False,
        )
    if state == "unknown":
        return RedisPresentation(
            "configured",
            "Status unavailable",
            "Paddock could not query the user systemd manager.",
            "dialog-warning-symbolic", "warning", False, False, False, False,
        )
    return RedisPresentation(
        "configured", "Stopped", "Redis is configured but not currently running.",
        "media-playback-stop-symbolic", None, True, False, False, False,
    )
