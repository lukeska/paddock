# Experiment 0.9: systemd Lifecycle and Recovery

## Status

- State: passed
- Date started: 2026-08-16
- User linger: disabled
- Selected availability boundary under test: system boot

## Ownership hypothesis

Use a root-owned `paddock.target` to order fixed service units. Run Caddy and
each PHP-FPM version from system units under the desktop user's UID/GID. Run the
minimal DNS responder and DNS-route integration in system scope.

Although Caddy and PHP state remain user-owned, placing their process units in
the system manager avoids unreliable cross-manager dependencies and does not
require user lingering. The application processes remain unprivileged; only
Caddy receives `CAP_NET_BIND_SERVICE` as established in ADR 0003.

## Why not user units

- Linger is disabled on the test system.
- User units would stop at the logout boundary and cannot natively depend on or
  order themselves with system DNS and low-port units.
- The user manager cannot grant Caddy a capability absent from the binary.
- Enabling linger solely for Paddock adds user-manager boot state and keeps
  unrelated enabled user units alive.

## Probe stages

1. Transient system units running as the desktop user: ordering, independent
   failure, automatic recovery, stop/restart behavior, and journald evidence.
2. Runtime-enabled unit files: enable/disable and target behavior.
3. Approved login/logout and reboot rehearsal after reversible cleanup is
   established.

Stage 1 status: passed.
Stage 2 status: passed.
Stage 3 status: passed and cleanup verified.

## Required policies

- One FPM unit per installed PHP minor version.
- `Restart=on-failure` with bounded rate limiting; clean stops do not restart.
- Caddy starts after required FPM sockets are ready, but one later FPM failure
  must not terminate Caddy or other PHP versions.
- Logs go to journald for process lifecycle plus user-owned files for Caddy
  access logs and PHP application/runtime diagnostics.
- Invalid Caddy config is rejected before reload; last-known-good remains live.
- Uninstall disables/stops the target before removing only Paddock units.

## Hardening finding

The first transient run correctly hit its bounded start limit rather than
looping forever: `ProtectSystem=strict` made global `/tmp` read-only while
OPcache defaulted `opcache.lockfile_path` to `/tmp`. Each FPM unit must set the
lock path to its narrow writable Paddock runtime directory. Journald exposed
the exact error and restart counters; cleanup removed all failed units.

A second run found that `caddy validate` is not side-effect free when file log
writers are configured: validation run as root created root-owned access logs,
then user-run Caddy could not open them. Validation must run with the same UID,
environment, and writable paths as the serving process. Lifecycle probes must
also assert initial route health before injecting failures.

Transient units are discarded after a clean stop, so an installation-free
probe must recreate the same transient definition to model `start`. Persistent
installed units will use normal `systemctl start` after `stop`.

## Transient lifecycle results

- PHP 8.4 and PHP 8.5 ran as separate constrained system units under the
  desktop user's UID/GID.
- An external `SIGKILL` terminated only the PHP 8.4 master. systemd recorded
  `code=killed, status=9/KILL`, incremented its restart counter to one, and
  restored the service automatically.
- PHP 8.5 remained healthy throughout the PHP 8.4 crash and recovery.
- A clean PHP 8.5 stop produced the expected 502 only for its site; recreating
  the transient unit restored it while PHP 8.4 stayed healthy.
- A later clean PHP 8.4 stop did not trigger `Restart=on-failure`; PHP 8.5 and
  Caddy remained active until normal probe cleanup.
- Caddy logs identified the exact unavailable Unix socket during deliberate
  backend outages.
- Bounded restart policy was independently proven when the initial OPcache
  hardening error hit `start-limit-hit` after three attempts rather than
  looping indefinitely.
- Final cleanup left no lifecycle units, sockets, listeners, or temporary
  directories.

Probe result:

```text
transient lifecycle passed php84_restarts=1 php85_independent=yes journald=visible clean_stop=no-restart
```

## Runtime-enabled lifecycle results

- Root-owned unit definitions and a fixed socket-readiness helper were installed
  only under `/run`; `/etc/systemd/system` was not modified.
- `systemctl enable --runtime` reported `enabled-runtime` for the aggregate
  target.
- FPM `ExecStartPost` socket gates combined with Caddy `After=` ordering made
  both sites healthy when target startup completed.
- Because Caddy only `Wants=` the FPM units, stopping PHP 8.4 kept Caddy and PHP
  8.5 active; app A returned 502 and app B remained healthy.
- Ordinary `systemctl start` restored the installed PHP 8.4 unit and its site.
- Caddy restarted cleanly without disturbing either backend.
- `PartOf=paddock-lifecycle.target` made target stop shut down all three
  services.
- Runtime disablement returned the target to `disabled`, and cleanup removed
  every `/run` unit, helper, listener, and temporary state directory.

Probe result:

```text
runtime-enabled lifecycle passed enable=runtime target-ordering=yes stop-start=yes target-stop=yes disable=yes
```

## Reboot results

- Persistent probe units were installed under `/etc/systemd/system` only after
  explicit approval, with an exact cleanup script prepared in advance.
- The probe copied its two runtimes and fixtures into the isolated, mode-`0700`
  `~/.local/share/paddock/reboot-probe` directory so no `/tmp` artifact was
  required after boot.
- Pre-reboot boot ID:
  `5a314e6c-8399-48c4-873c-e35b48cd68a0`.
- Post-reboot boot ID:
  `90559de0-97d7-452a-b3fa-ad311739be0b`.
- User linger remained disabled.
- Both PHP-FPM units and Caddy entered active state at `18:13:25`, during the
  system boot, with zero restarts.
- Caddy's boot journal shows validation completed before its serving process
  started, after both socket-gated FPM start jobs.
- Post-reboot requests returned PHP 8.4.23 and PHP 8.5.8 from their respective
  sites without any manual service command.
- The service processes continued to run as the desktop user even though the
  unit owner and availability boundary were system scope.

## Final cleanup

- The persistent target was disabled and stopped.
- All four `/etc/systemd/system/paddock-reboot*` files were removed.
- The fixed `/usr/local/lib/paddock-reboot-wait-for-socket` helper was
  removed.
- The copied runtimes, fixtures, logs, sockets, and state directory were
  removed.
- No lifecycle listener or loaded unit remained.

Decision: [ADR 0005](../../../docs/adr/0005-service-ownership.md).
