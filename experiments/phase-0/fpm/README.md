# Experiment 0.4: PHP-FPM Process and Socket Model

## Status

- State: passed
- Date started: 2026-08-16
- Caddy tested: 2.11.4-1 from the official Arch package
- System changes made by this experiment: Caddy package installed through
  Omarchy; its system service remains inactive and was not used

## Question

What is the smallest reliable FPM topology that routes sites to different PHP
versions while preserving useful isolation and simple lifecycle management?

## Models

### One master and one socket per PHP version

Every site assigned to PHP 8.4 shares the PHP 8.4 master and socket; PHP 8.5 has
an independent master and socket. Caddy selects a socket from the site's runtime
assignment.

Advantages:

- Small process and configuration surface.
- Version restart affects only sites using that version.
- Runtime selection is an explicit socket mapping.
- One user service per installed version maps naturally to systemd.

Limitations:

- Sites on the same PHP version share worker settings and environment policy.
- Per-site PHP limits require Caddy FastCGI parameters, `.user.ini`, or a later
  dedicated-pool escape hatch.
- A failed version master affects all sites assigned to that version.

### One pool per site

This offers finer worker limits and log attribution but substantially increases
generated state, idle workers, validation paths, and service lifecycle work.
It should not be the default unless tests uncover a concrete isolation need.

### One master per version with generated site pools

This is a reasonable future advanced mode, but a bad pool include can prevent
the whole version master from starting. It retains much of the configuration
cost of per-site pools without process-level failure isolation.

## Evidence already collected

The PHP runtime experiment started PHP 8.4.23 and 8.5.8 FPM masters
simultaneously with:

- Separate user-owned Unix sockets.
- Separate PID files and error logs.
- Strictly validated configuration before startup.
- Matching CLI/FPM patch versions.
- Clean bounded shutdown and temporary-state cleanup.
- Version-local `php.ini`, `conf.d`, and optional-extension activation.

The two runtime roots were also shown to be operationally independent: making
the entire PHP 8.4 installation unavailable did not prevent PHP 8.5 from
running its configuration probe or Laravel test suite.

## Selected model

Use one user-owned FPM master and one Unix socket per installed PHP version.
Generate no per-site pools in the default path. Keep a dedicated-pool design as
an explicit future escape hatch for sites requiring worker-level settings or
stronger process isolation.

Proposed runtime paths:

```text
$XDG_RUNTIME_DIR/paddock/php/8.4/fpm.sock
$XDG_RUNTIME_DIR/paddock/php/8.4/php-fpm.pid
$XDG_RUNTIME_DIR/paddock/php/8.5/fpm.sock
$XDG_RUNTIME_DIR/paddock/php/8.5/php-fpm.pid
```

Persistent logs and generated configuration belong under Paddock-owned data
and state directories, never `/run`, `/etc/php`, or system PHP directories.

FPM must use `clear_env = yes`. Application secrets should be read by the
application from its project environment file or an explicit future secret
provider, not inherited wholesale from the Paddock service manager. Only
reviewed, generated `env[...]` entries may be added if a concrete integration
requires them.

## End-to-end result

An unprivileged Caddy 2.11.4 process listened on loopback port 18080 and routed:

| Host | Runtime | Upstream |
| --- | --- | --- |
| `app-a.test` | PHP 8.4.23 | private Unix socket A |
| `app-b.test` | PHP 8.5.8 | private Unix socket B |

The probe verified static files, Laravel-style front-controller fallback,
nested routes, forwarded scheme data, and per-site JSON access logs. Both FPM
configurations were validated before startup.

When the PHP 8.4 master stopped, app A returned 502 and Caddy's log named the
failed 8.4 socket. App B continued returning 200 through PHP 8.5. Restarting only
PHP 8.4 recreated its mode-0600 socket and restored app A without interrupting
app B.

This validates the default one-master/one-socket-per-version model. Dedicated
site pools remain an advanced future feature, not part of the initial design.

## Follow-up for service packaging

1. Repeat lifecycle checks using the final systemd user units and
   `$XDG_RUNTIME_DIR` paths.
2. Measure full master-plus-worker RSS across idle and loaded states.
3. Validate behavior across logout/login and linger policy decisions.

## Package prerequisite

Caddy was installed through Omarchy in an interactive terminal:

```bash
omarchy pkg add caddy
```

Installing the package may create a system service, but this experiment will not
enable or use it. Tests will run an unprivileged, user-owned Caddy process on
high ports with configuration stored in this workspace.
