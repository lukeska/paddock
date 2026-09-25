"""Explain failures to reach the desktop user's systemd manager."""

BUS_HINT = (
    "Cannot reach your user systemd session. If you switched users with "
    "'su -', run: export XDG_RUNTIME_DIR=/run/user/$(id -u); "
    "export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus"
)


def bus_unreachable(stderr: str) -> bool:
    message = stderr.lower()
    return (
        "failed to connect to bus" in message
        or "failed to connect to user scope bus" in message
        or "no medium found" in message
    )


def systemctl_error(stderr: str, stdout: str, fallback: str) -> str:
    if bus_unreachable(stderr):
        return BUS_HINT
    return stderr.strip() or stdout.strip() or fallback
