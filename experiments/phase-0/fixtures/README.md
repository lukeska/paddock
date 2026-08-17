# Experiment 0.2: Minimal Application Fixtures

## Result

- Status: implemented and locally validated
- Date: 2026-08-15
- System changes made: none
- Runtime used for validation: foreign Herd Lite PHP 8.5.0, read-only
- Cleanup required: none; smoke-test processes and temporary logs self-remove

These dependency-free fixtures isolate web-server, FastCGI, routing, logging,
and PHP-version behavior from Laravel, Composer, databases, and Node.js.

## Layout

```text
fixtures/
├── app-php-a/public/
│   ├── fixture.txt
│   └── index.php
├── app-php-b/public/
│   ├── fixture.txt
│   └── index.php
├── shared/
│   ├── front-controller.php
│   └── router.php
└── smoke.sh
```

The two applications intentionally share behavior while declaring different
fixture identities. Later experiments will route each host to a different
PHP-FPM socket and assert that `/runtime` reports the assigned version.

## Endpoints

| Path | Expected behavior |
| --- | --- |
| `/health` | Plain-text health response with fixture identity |
| `/runtime` | PHP version, SAPI, document root, host, and selected forwarded headers |
| `/fixture.txt` | Static response that should bypass PHP under Caddy |
| `/nested/path` | JSON proving front-controller fallback |
| `/failure` | HTTP 500 plus an attributable error-log entry |

The runtime endpoint deliberately reports only infrastructure facts. It does not
dump the environment, server variables, filesystem contents, or secrets.

## Local validation

Provide an explicit PHP binary so the test never silently adopts system PHP:

```bash
PHP_BIN=/absolute/path/to/php ./experiments/phase-0/fixtures/smoke.sh
```

Optional ports can avoid local conflicts:

```bash
PORT_A=28081 PORT_B=28082 PHP_BIN=/absolute/path/to/php \
  ./experiments/phase-0/fixtures/smoke.sh
```

The script:

1. Syntax-checks every PHP file.
2. Starts two temporary PHP development servers on loopback.
3. Checks identity, runtime data, nested routing, and static files.
4. Verifies deliberate failures return 500 and reach the correct log.
5. Terminates both servers and removes temporary logs on every exit path.

The built-in PHP server is only a fixture validator. It is not a candidate for
Paddock's production request path.

## Caddy/FPM use in later experiments

For Caddy, each site's root will be its `public/` directory. Equivalent routing
must:

- Serve an existing `fixture.txt` directly.
- Rewrite unknown paths to `index.php` while preserving request information.
- Send PHP scripts to the FPM socket assigned to that site.
- Preserve the host and forwarded scheme.
- Attribute access and error logs to the correct fixture.

`shared/router.php` exists only for the built-in validation server and must not be
used as Caddy's router.

## Real Laravel validation

A minimal real Laravel fixture remains intentionally deferred until the PHP
runtime strategy and Composer execution are selected in Experiment 0.3. That
validation must cover:

- Composer platform requirements and autoloading.
- `public/index.php` routing.
- Writable `storage/` and bootstrap cache paths.
- A normal Laravel health route.
- Absence of database and frontend-build requirements.

Deferring it prevents a Composer download or framework dependency from obscuring
the infrastructure fixture result.

## Decision

The two minimal fixtures are suitable for PHP runtime, FPM, Caddy, logging, and
version-isolation experiments. They have no external runtime dependency beyond
PHP itself and expose deterministic assertions for the Phase 0 smoke test.

The next task is Experiment 0.3: evaluate sources for two simultaneous,
maintainable PHP runtimes on Omarchy/Arch Linux.
