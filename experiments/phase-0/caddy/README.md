# Experiment 0.5: Caddy Routing and Reload Safety

## Status

- State: passed
- Date started: 2026-08-16
- Caddy: 2.11.4-1 from the official Arch package
- Privilege model: user-owned process on unprivileged loopback ports

## Configuration model

Caddy receives one generated site block per linked project. Each block owns its
document root, PHP-version socket mapping, and JSON access log. PHP requests use
`php_fastcgi` for Laravel-style front-controller fallback; static files use
Caddy's file server directly.

The generated configuration and Caddy admin endpoint remain user-owned. Every
candidate must pass `caddy validate` before a reload is attempted. A reload is
successful only after health checks confirm the expected configuration
generation. The last known-good configuration remains active if validation or
reload fails.

## Shared probe

The end-to-end probe currently lives at `../fpm/probe.sh` because Experiments
0.4 and 0.5 exercise the same live topology. It tests:

- Two hosts routed to distinct PHP-version Unix sockets.
- Static files and Laravel-style front-controller routes.
- Forwarded scheme data.
- Per-site JSON request logging.
- Targeted PHP-version failure and recovery.
- Validated Caddy reload while the unaffected site receives requests.
- Rejection of an invalid directive during validation and reload.
- Preservation of the last known-good generation after rejection.

## Result

The initial generation served both fixtures correctly through PHP 8.4.23 and
8.5.8. A second configuration was validated and atomically reloaded while a
request loop continuously checked the unaffected PHP 8.5 site; no request
failed. Response headers confirmed that generation 2 became active.

A third configuration replaced `php_fastcgi` with an unknown directive. Both
`caddy validate` and `caddy reload` rejected it. The running process continued
serving both sites with HTTP 200 responses, and response headers confirmed that
generation 2 remained active.

The same run also proved actionable upstream failure behavior: stopping the PHP
8.4 master returned 502 only for its site, while Caddy logged the missing 8.4
Unix socket and PHP 8.5 remained healthy.

## Remaining work

1. Exercise a larger concurrent request window during reload.
2. Decide whether the production admin endpoint should use a Unix socket rather
   than a loopback TCP port.
3. Move repeated process helpers into shared Phase 0 tooling if another
   experiment needs them.
