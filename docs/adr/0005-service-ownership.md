# ADR 0005: Service Ownership and Lifecycle

- Status: accepted for implementation
- Date: 2026-08-16
- Experiment: [systemd lifecycle and recovery](../../experiments/phase-0/lifecycle/README.md)

## Context

Paddock coordinates a loopback DNS responder, a route-only DNS integration,
Caddy, and one PHP-FPM master per installed PHP minor version. These components
must start without a terminal, recover independently, expose actionable logs,
and survive the selected availability boundary.

User lingering is disabled by default on the tested Omarchy system. Splitting
dependent processes between system and user managers would make boot ordering
and recovery implicit, and enabling linger for Paddock would also keep
unrelated enabled user units alive. The user manager also cannot grant Caddy a
capability that the binary does not possess.

## Decision

Use system boot as the availability boundary. A root-owned
`paddock.target` coordinates fixed system units, while application processes
run under narrowly selected identities:

- DNS responder: system unit with only the privilege/identity required to bind
  loopback port 53; it serves `.test` only and has no upstream forwarding.
- DNS route integration: fixed root-owned helper/unit because NetworkManager
  connection state is system scope.
- Caddy: system unit running as the desktop user with only
  `CAP_NET_BIND_SERVICE`, as defined by ADR 0003.
- PHP-FPM: one system unit per installed PHP minor version, each running as the
  desktop user with no capabilities.

System scope owns unit definitions, ordering, startup, and the minimal network
boundary. The desktop user owns mutable configuration, certificates, runtime
state, project access, sockets, and application logs. No web or PHP process
runs as root.

## Dependency graph

```text
network-online.target
        |
        +--> Paddock DNS responder --> route-only ~test integration
        |
        +--> PHP-FPM 8.4 --socket ready--+
        +--> PHP-FPM 8.5 --socket ready--+--> Caddy
                                             |
                                      paddock.target
```

Each FPM unit uses a socket-readiness `ExecStartPost` gate. Caddy orders itself
after those start jobs, but uses `Wants=` rather than a permanent `Requires=`
relationship. A later FPM outage therefore yields a diagnosable 502 only for
sites using that version; Caddy and other PHP versions remain available.

Every component uses `PartOf=paddock.target` so stopping the target shuts down
the full stack. Starting the target requests every component in dependency
order.

## Restart and failure policy

- Use `Restart=on-failure`, never unconditional restart.
- Use a nonzero restart delay and bounded `StartLimitIntervalSec` /
  `StartLimitBurst` values.
- A clean administrative stop must not restart the service.
- PHP versions recover independently.
- Caddy validates configuration as the same user, environment, and writable
  filesystem view as its serving process before start or reload.
- Invalid Caddy configuration never replaces the last-known-good generation.
- OPcache lock files live in the unit-owned writable runtime directory, not
  global `/tmp`.

## Logging policy

systemd lifecycle, exit reason, restart count, and startup errors go to
journald under stable unit names. Caddy access logs remain user-owned and
per-site. FPM error logs and application diagnostics remain attributable to a
specific PHP version. CLI status commands may summarize journals but must not
hide the original unit and socket identifiers.

## Login and reboot semantics

The stack starts during system boot when `paddock.target` is enabled. It does
not depend on a graphical session, open terminal, user manager, or linger.
Logging out does not stop development services. This is intentional for a
single-user Omarchy workstation and must be disclosed during installation.

## Installation and uninstall

Installation adds only reviewed Paddock unit files and fixed helpers, reloads
systemd, and enables the aggregate target after configuration validation.
Package-owned Caddy units and unrelated user services are never modified.

Uninstall performs the reverse order:

1. Disable and stop `paddock.target`.
2. Remove only Paddock unit files and fixed helpers.
3. Reload systemd and verify no Paddock unit or listener remains.
4. Handle user-owned runtimes, logs, certificates, and CA material through
   separate explicit retention/removal choices.

## Consequences

- Sites are available after boot without enabling user linger.
- PID 1 owns lifecycle but Caddy/PHP still execute with the desktop user's
  permissions.
- The implementation is intentionally single-desktop-user; multi-user serving
  requires a different port and ownership design.
- Unit generation must know the selected desktop UID/GID and stable state
  paths at installation time.
- Root-owned units must never interpolate mutable executable paths or execute
  project hooks.

## Evidence

- An external PHP 8.4 `SIGKILL` produced a visible journald failure and exactly
  one automatic restart; PHP 8.5 remained healthy.
- Clean stop did not restart a service.
- Bounded startup failures reached `start-limit-hit` instead of looping.
- Runtime-installed units passed enable, target ordering, ordinary stop/start,
  aggregate stop, disable, and complete removal.
- A real reboot changed boot ID from
  `5a314e6c-8399-48c4-873c-e35b48cd68a0` to
  `90559de0-97d7-452a-b3fa-ad311739be0b`.
- With linger still disabled, both FPM units and Caddy activated during boot at
  the same timestamp, had zero restarts, and served PHP 8.4.23 and 8.5.8.
- Final cleanup left no unit, helper, state directory, socket, or listener.

