# The project file

`paddock.yml` sits in a project's repository and describes what the project
needs locally. `paddock init` reads it and brings the machine in line.

```yaml
name: my-app          # optional; defaults to the directory name
php: "8.5"            # optional; quoted, because YAML reads 8.5 as a number
node: "24"             # optional Node.js major; also detects .nvmrc/.node-version
secure: true          # optional; default false

services:             # optional
  postgres:
    version: "17"     # optional; defaults to the catalog version
    port: 5432        # optional; defaults to the catalog port
  redis:              # an empty body means "the defaults"
```

Supported services are `mysql`, `postgres`, and `redis`. `version` replaces
only the image tag; the registry and repository stay Paddock's, so a project
file cannot point the machine at an arbitrary image.

## Reverb workers

Reverb belongs to a Laravel site rather than the shared supporting-service
catalog. Paddock detects `laravel/reverb` in Composer metadata (or
`BROADCAST_CONNECTION=reverb` in `.env`) and exposes a worker for that site:

```bash
paddock worker start reverb
paddock worker stop reverb
paddock worker restart reverb my-app
paddock worker logs reverb my-app
```

The first start allocates an unused loopback port beginning at 8080. Paddock
sets the Reverb server address and the client-facing host, port, and scheme in
`.env`, while leaving unrelated values and Reverb credentials untouched. It
proxies Reverb's `/app/*` and `/apps/*` endpoints through the site's existing
`.test` HTTP or HTTPS address. The worker runs with the PHP version selected
for that site.

## Queue workers

Every detected Laravel site also exposes a queue worker with the same
site-scoped lifecycle:

```bash
paddock worker start queue
paddock worker stop queue
paddock worker restart queue my-app
paddock worker logs queue my-app
```

It runs `artisan queue:work` with the PHP version selected for the site. Queue
workers need no published port or Caddy route.

## Applying it

```bash
paddock init            # converge
paddock init --dry-run  # report what would change, and change nothing
```

Each line is marked with what happened:

```
+ link /home/you/code/my-app as my-app.test      changed
= my-app.test already served over HTTPS          already correct
! postgres is already running postgres:17 ...    declined
```

`init` is idempotent: run it twice and the second run reports only `=`. It
exits 1 if anything was declined, so a script can tell the difference between
"converged" and "converged as far as it could".

## What it will not do

Supporting services are one shared instance per machine, by ADR 0010. A
project asking for PostgreSQL 16 on a machine already running 17 gets a `!`
line naming both, and nothing changes — imposing it would silently repoint
every other project's database. Resolve it by agreeing on a version, or by
running the odd one out on its own port.

`init` also refuses to take a site name that already serves a different
directory.

Nothing here writes your `.env`. `paddock service add` prints the connection
settings; which of them a project wants is the project's business.

## Strictness

An unknown key is an error rather than something quietly ignored, because a
typo in a committed file should fail on the first machine that reads it rather
than do nothing on all of them. Keys that are planned but not yet implemented —
`aliases`, `env` — say so specifically rather than reading as typos.
