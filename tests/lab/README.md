# Paddock compatibility lab

This manual lab exercises Paddock against real Laravel applications, every
supported PHP and Node line, every supporting service, and all three site
workers. Canonical source stays here in Git. The controller copies it into the
parked directory; never edit these templates through the generated copies.

The two sites intentionally cover different framework generations:

| Site | Framework | PHP matrix | Default Node | Reverb |
|---|---|---|---|---|
| `paddock-lab.test` | Laravel 13 | 8.3, 8.4, 8.5 | 24 | yes |
| `paddock-lab-legacy.test` | Laravel 9 | 8.0, 8.1, 8.2 | 22 | no |

Laravel 9 is end-of-life and has known security advisories. Composer's
advisory blocker is disabled only in that fixture so its historical lock can
still be reproduced. The lab listens through Paddock's local `.test` stack and
must never be deployed or exposed to a network.

## Install and use

Build and install the current Paddock package first, then run:

```bash
./tests/lab/paddock-lab install
./tests/lab/paddock-lab status
./tests/lab/paddock-lab check
./tests/lab/paddock-lab open current
```

`install --park PATH` uses and registers another parked directory. Set
`PADDOCK_LAB_PARK=PATH` for subsequent commands against that custom location.
Installation is resumable: successful runtime and service work is
recorded before the next operation begins.

The full command surface is:

```text
paddock-lab install [--park PATH]
paddock-lab sync [--force]
paddock-lab status
paddock-lab check [current|legacy|all]
paddock-lab php-matrix
paddock-lab node-matrix
paddock-lab open current|legacy|mailpit|meilisearch|rustfs
paddock-lab reset [--yes]
paddock-lab remove [--yes]
```

`sync` copies managed source changes but preserves `.env`, dependencies,
storage, caches, built assets, and local databases. It stops on a locally
edited managed file; inspect the difference before using `sync --force`.

`reset` removes lab records and probe data while retaining dependencies and
containers. `remove` checks UUID ownership before it stops workers, unlinks
sites, deletes generated projects, and deletes the seven dedicated service
volumes. Neither command touches an unmarked project or an unrecorded service.

## Fixed service ports

| Service | Port | UI |
|---|---:|---:|
| Redis | 16379 | — |
| MySQL | 13306 | — |
| PostgreSQL | 15432 | — |
| Mailpit | 11025 | 18025 |
| Meilisearch | 17700 | 17700 |
| Typesense | 18108 | — |
| RustFS | 19000 | 19001 |

A collision aborts installation and names the port. Paddock never silently
moves a lab service because stable `.env` values are part of the test.

## What a check proves

Both `php artisan paddock:smoke` and the `/paddock` dashboard perform real
round trips through MySQL, PostgreSQL, Redis, Mailpit, Meilisearch, Typesense,
RustFS, the queue, and the scheduler. They also report PHP/FPM, required
extensions, frontend build Node, routing, TLS context, and assets. The current
app additionally checks its Reverb worker and WebSocket port.

`php-matrix` selects each compatible PHP minor, re-links the site, checks the
Composer platform, runs CLI checks, then queries the HTTP endpoint to prove
FPM agrees. `node-matrix` builds both applications under Node 22 and 24.
Defaults are restored in a `finally` path. Missing runtimes print `SKIP` and
produce exit code 2; probe failures produce exit code 1.

## Manual acceptance

1. Keep an unrelated parked project and service present while installing.
2. Open both HTTPS dashboards and inspect each card.
3. Run `check`, `php-matrix`, and `node-matrix`.
4. Stop queue, scheduler, Reverb, and services in the TUI and verify failures
   recover after restart.
5. Change PHP and Node from the TUI and confirm the reported versions.
6. Run `sync` twice, edit a managed file, then test conflict and `--force`.
7. Run `reset` and verify the containers remain.
8. Run `remove` and verify unrelated projects, instances, and volumes remain.
