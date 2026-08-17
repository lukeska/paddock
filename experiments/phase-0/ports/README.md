# Experiment 0.8: Ports 80 and 443

## Status

- State: passed
- Date started: 2026-08-16
- Existing listeners on 80/443: none
- Kernel unprivileged-port floor: 1024
- Packaged Caddy service: disabled and inactive
- Caddy binary file capabilities: none

## Candidate assessment

### File capability on `/usr/bin/caddy`

Not preferred. It grants low-port binding to every invocation of the shared
binary and package upgrades can replace the inode and silently remove the
capability.

### Dedicated system unit with ambient capability

Leading candidate. A fixed root-owned unit launches Caddy as the desktop user,
with only `CAP_NET_BIND_SERVICE` in its ambient and bounding sets. Caddy parses
user-owned generated configuration as the user, not as root. The unit must not
execute project files or accept arbitrary command paths from mutable config.

### Packaged system Caddy service

The Arch unit is well hardened and already grants only low-port binding, but it
runs as the separate `caddy` user with `ProtectHome=true`. Using it would move
configuration and certificate ownership into system scope and complicate the
single-desktop-user development model.

### Socket activation or packet forwarding

Retained only if the ambient-capability unit fails. Both add a privileged
broker/forwarding layer and more installation and conflict-removal state.

## Test invariants

- Bind only `127.0.0.1:80` and `127.0.0.1:443`.
- Caddy's real/effective UID remains the desktop user's UID.
- Effective and bounding capabilities contain only `CAP_NET_BIND_SERVICE`.
- `NoNewPrivileges` is enabled.
- HTTP redirects to trusted HTTPS.
- A pre-existing listener causes a clear start failure and is never terminated.
- Cleanup removes only the transient test unit and temporary leaf material.

## Results

- A transient system unit launched `/usr/bin/caddy` as UID/GID 1000.
- `CapInh`, `CapPrm`, `CapEff`, `CapBnd`, and `CapAmb` were all exactly
  `0000000000000400` (`CAP_NET_BIND_SERVICE`).
- `NoNewPrivs` was `1`; `PrivateDevices=yes`, `ProtectSystem=strict`, and
  `ProtectHome=read-only` were active.
- Narrow `ReadWritePaths` exceptions for Caddy's data and config directories
  eliminated storage warnings without widening filesystem access.
- Caddy bound only loopback TCP 80, TCP 443, and UDP 443.
- HTTP returned a permanent redirect to `https://app-a.test/`; default curl
  trust loaded the HTTPS response successfully.
- A second Caddy process did **not** fail on occupied ports. Caddy uses
  `SO_REUSEPORT`, so multiple Caddy processes can silently share the listeners.
- An explicit root-run preflight reported the existing process name, PID, and
  file descriptors for TCP 80, TCP 443, and UDP 443. It never terminated the
  owner.
- After stopping only the transient Paddock units, the preflight reported all
  three sockets available and temporary certificate material was removed.
- The packaged Caddy service and `/usr/bin/caddy` capabilities were unchanged.

## Decision

Use a fixed root-owned system unit that runs Caddy as the desktop user with only
`CAP_NET_BIND_SERVICE`. A fixed root-owned preflight helper checks TCP 80, TCP
443, and UDP 443 before launch and refuses conflicts explicitly. Caddy reads and
validates generated configuration as the unprivileged user.

Decision: [ADR 0003](../../../docs/adr/0003-http-server.md).

