# ADR 0003: HTTP Server and Standard-Port Boundary

- Status: accepted for implementation
- Date: 2026-08-16
- Experiments: [Caddy routing](../../experiments/phase-0/fpm/README.md),
  [reload safety](../../experiments/phase-0/caddy/README.md), and
  [ports 80/443](../../experiments/phase-0/ports/README.md)

## Context

Paddock needs one local HTTP server that routes linked `.test` sites to their
selected PHP-FPM versions, reloads without interrupting healthy sites, and
serves trusted HTTP/HTTPS on conventional ports. Linux restricts ports below
1024, but the mutable generated site map and project paths must never be parsed
or executed by a root web-server process.

The packaged Caddy binary has no file capabilities. Arch's packaged service
uses a separate `caddy` account, `CAP_NET_BIND_SERVICE`, and strong hardening,
but `ProtectHome=true` intentionally prevents the development server from
reading user-owned project and certificate state.

## Decision

Use Caddy as the sole Paddock HTTP server. Launch it from a fixed, root-owned
systemd unit as the desktop user. Grant the process only
`CAP_NET_BIND_SERVICE` through the unit's ambient and bounding capability sets.
Do not set a capability on `/usr/bin/caddy`.

The unit's executable and argument paths are fixed at installation. Caddy,
running with the user's real/effective UID and GID, validates and interprets the
generated configuration and accesses project, socket, certificate, and log
paths. PID 1 grants the low-port capability but never interprets project files
or mutable Caddy directives.

Bind HTTP, HTTPS, and HTTP/3 only to loopback by default:

- `127.0.0.1:80/tcp`
- `127.0.0.1:443/tcp`
- `127.0.0.1:443/udp`

IPv6 loopback may be added only after explicit listener and DNS coverage.

## Unit security boundary

The implementation unit must include at least:

- `User=<desktop user>` and the matching primary group.
- `AmbientCapabilities=CAP_NET_BIND_SERVICE`.
- `CapabilityBoundingSet=CAP_NET_BIND_SERVICE`.
- `NoNewPrivileges=true`.
- `PrivateDevices=true`.
- `ProtectSystem=strict` and a read-only home policy compatible with serving
  explicitly selected user paths.
- Narrow `ReadWritePaths` for Paddock-owned Caddy data, generated state, and
  logs only.
- A validation step before start/reload and last-known-good configuration for
  rollback.

No shell, project executable, hook, or mutable command path may appear in the
system unit. PHP runs separately as the user through Unix FPM sockets.

## Conflict policy

Run a fixed root-owned, read-only preflight before starting Caddy. It checks
TCP 80, TCP 443, and UDP 443, reports listener address plus process/PID when
available, and exits nonzero on any owner. Paddock never kills, disables, or
reconfigures an existing listener automatically.

Explicit preflight is mandatory because Caddy enables `SO_REUSEPORT`: a second
Caddy instance can silently share ports rather than returning `EADDRINUSE`.

## Rejected alternatives

- **File capability on `/usr/bin/caddy`:** grants every invocation the
  capability and package replacement can silently remove it.
- **Packaged `caddy.service`:** its separate user and protected home conflict
  with the user-owned development state model.
- **Root Caddy process:** violates the rule against privileged interpretation of
  mutable user configuration and project paths.
- **Packet forwarding from high ports:** adds persistent privileged firewall
  state and makes ownership/conflict diagnosis less direct.
- **Socket activation:** retained as a fallback only; Caddy's listener and
  graceful-reload model already works with the narrower ambient capability.

## Consequences

- Package upgrades do not affect the port capability because it belongs to the
  unit, not the binary inode.
- Caddy remains an unprivileged user process except for low-port binding.
- Installation/removal must add/remove one reviewed system unit and preflight
  helper without modifying Arch's packaged Caddy unit.
- The single-desktop-user assumption must be explicit. Multi-user ownership
  needs a separate design rather than multiple instances sharing the ports.
- Reload generation and validation remain user-owned operations; system scope
  is limited to launch and the port boundary.

## Evidence

- Effective UID/GID remained 1000/1000.
- Every capability set contained only hexadecimal `0x400`,
  `CAP_NET_BIND_SERVICE`.
- HTTP redirected to trusted HTTPS and Caddy served successfully on 80/443.
- Invalid generated configuration was previously rejected while the last-known
  good generation stayed active.
- Explicit preflight reported the exact Caddy PID and descriptors during a
  conflict, then reported all sockets free after cleanup.

