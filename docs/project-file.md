# The project file

`paddock.yml` sits in a project's repository and describes what the project
needs locally. `paddock init` reads it and brings the machine in line.

```yaml
name: my-app          # optional; defaults to the directory name
php: "8.5"            # optional; quoted, because YAML reads 8.5 as a number
node: "24"             # optional Node.js major; also detects .nvmrc/.node-version
secure: true          # optional; default false
type: laravel         # optional; detected from the project when omitted
root: public          # optional; the type's own default when omitted
nginx: .paddock/nginx.conf   # optional; inert until trusted, see below
client_max_body_size: 512m   # optional; upload limit for this site

env:                  # optional; quoted strings written to the local .env
  APP_ENV: local
  APP_NAME: "My App"
  FEATURE_FLAG: "true"

services:             # optional
  postgres:
    version: "17"     # optional; defaults to the catalog version
    port: 5432        # optional; defaults to the catalog port
  redis:              # an empty body means "the defaults"

workers:              # optional Laravel site workers
  queue:              # starts now and at login; autostart defaults to true
  scheduler:
    autostart: true
  reverb:
    autostart: false  # starts now, but not automatically after login
```

## Project types

`type` selects the web-server rules a project is served with. Paddock detects
it from files a repository commits, so a fresh clone is identified correctly
before anything is installed:

| type | detected from | document root |
|---|---|---|
| `laravel` | `artisan` | `public` |
| `statamic` | `artisan` and `please` | `public` |
| `wordpress` | `wp-config.php` | the project root |
| `symfony` | `bin/console` and `public/index.php` | `public` |
| `php` | `public/index.php`, or `index.php` | `public`, else the project root |
| `static` | nothing else matched | `public`, `dist`, `build`, else the root |

`static` serves files and runs nothing. Because it is also the fallback for a
directory nothing else matched, it refuses `.php` outright rather than handing
back source.

Declaring `type` explicitly overrides detection, and an unknown one is an
error rather than something quietly ignored. `root` overrides the document
root; it is relative to the project and cannot leave it, because this file
arrives with a clone.

A type chosen explicitly — in this file or with `paddock link --type` — is
preserved by later links, so `paddock init` never silently reverts it.

## Local environment

`env` declares non-secret environment values that every local checkout of the
project should use. `paddock init` writes those keys to the project's `.env`:

```yaml
env:
  APP_ENV: local
  CACHE_STORE: redis
  SCOUT_DRIVER: typesense
  TYPESENSE_HOST: 127.0.0.1
  TYPESENSE_PORT: "8108"
```

Keys must be valid environment-variable names. Values must be YAML strings;
quote values such as numbers and booleans so YAML does not turn them into a
different type. Paddock encodes spaces and other special characters safely for
Laravel's dotenv parser.

When `.env` does not exist, Paddock starts it from `.env.example` when that file
is present, then applies the declaration. A newly created `.env` is readable
only by its owner. For an existing file, Paddock:

- changes active assignments for declared keys;
- appends declared keys that are missing;
- preserves comments, blank lines, unrelated values, and the file mode;
- makes duplicate active assignments consistent so a later one cannot win;
- reports no change when the result already matches.

The declaration is intentionally one-way and conservative. Removing a key from
`paddock.yml` does not delete it from `.env`, because the local value may have
been customized or adopted by another tool. Delete unwanted local keys
explicitly. Do not commit secrets to `paddock.yml`; it is part of the project
repository just like `.env.example`.

## Shipping nginx directives

`nginx` names a file of nginx directives, relative to the project, that the
repository carries for everyone working on it.

**Declaring it does not apply it.** Every other key here chooses between
options Paddock defines. This one is arbitrary server configuration arriving
with a `git clone`, and a fragment can address files outside the project, so it
stays inert until someone on this machine has read it:

```
paddock config              # what is declared, and whether it applies
paddock config trust        # apply it, after reading it
paddock config revoke       # stop applying it
```

`paddock link` and `paddock init` both record the declaration. `init` reports
it with `!` and exits 1 until it is trusted, the same as anything else it
declines to impose. Trust is recorded as the file's SHA-256, so an edit or a
pull that changes it withdraws trust automatically — nobody has to notice.

Trust is per machine. Cloning the same project elsewhere means reviewing it
there.

**Ship only what the site is correct without.** Because a fragment is withheld
until someone reviews it, it is inert on every fresh clone — the rules travel,
but they do not apply. That is fine for a header, a debug endpoint, or a
redirect used while developing: absent, the site still serves and nothing
misleads. It is the wrong home for anything the application *requires*, such as
a rewrite it depends on or a limit that makes an upload form work, because the
gate will withhold it and the resulting failure does not name its cause.

A requirement belongs in a named option instead, which travels *and* applies
because it can only name a value Paddock understands:

```
client_max_body_size: 512m   # 512m, 1g, or a plain byte count
```

That raises the upload limit for this site alone, overriding Paddock's default.
Where a directive is common enough that projects reach for a fragment to get
it, the answer is a new option rather than a wider escape hatch — so if you are
about to ship one for something every project needs, please open an issue.

Your own directives need no trust, because you wrote them:

```
paddock config edit         # your own fragment for this site
paddock reload              # manually retry or diagnose an automatic reload
```

Saved fragment changes are detected automatically. Paddock waits briefly for
atomic editor saves to settle, validates the complete candidate with `nginx -t`,
and reloads only when it is valid. A rejected edit leaves the last working
generation active; `paddock reload` remains available to retry it and print the
exact nginx error.

Both fragments are included at the end of the site's server block, the project's first
and yours last, so a plain directive overrides the generated one above it. A
`location` is different: nginx prefers the longest matching prefix and only
then tries regular expressions in order, so to beat one of Paddock's generated
regex locations use `^~` or an exact `=` match. Everything is validated with
`nginx -t` before it is promoted, so a mistake is reported against your file
and the site keeps serving what it served before.

Supported services are `mailpit`, `meilisearch`, `mysql`, `postgres`, `redis`,
`rustfs`, and `typesense`.
`version` replaces
only the image tag; the registry and repository stay Paddock's, so a project
file cannot point the machine at an arbitrary image.

## Declaring site workers

The `workers` mapping starts Laravel processes belonging to this site. Supported
workers are `queue`, `scheduler`, and `reverb`:

```yaml
workers:
  queue:
  scheduler:
    autostart: true
  reverb:
    autostart: false
```

An empty worker body uses the defaults. Declared workers are started whenever
`paddock init` runs. `autostart` controls whether the worker also starts with
the user's systemd session and defaults to `true`; setting it to `false` does
not stop a worker that is currently running.

Queue and scheduler declarations require a detected Laravel project. Reverb
also requires `laravel/reverb` in Composer metadata (or the existing Reverb
environment declaration). An unavailable declared worker is reported as
blocked, and `paddock init` exits non-zero instead of silently ignoring it.

Removing a worker from `paddock.yml` does not stop or delete an already
configured worker. This is deliberately conservative: declarations converge
what they contain without treating an omitted entry as permission to stop a
developer's local process. Use the worker controls in the TUI or
`paddock worker stop TYPE` explicitly.

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
workers need no published port or web-server route.

## Scheduler workers

Every detected Laravel site exposes a scheduler worker alongside its queue:

```bash
paddock worker start scheduler
paddock worker stop scheduler
paddock worker restart scheduler my-app
paddock worker logs scheduler my-app
```

It runs `artisan schedule:work --no-interaction` with the PHP version selected
for the site. Autostart can be enabled independently from the queue and Reverb.

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
directory. Service connection settings are not injected automatically;
`paddock service add` prints them, and a project can explicitly declare the
ones it wants under `env`.

## Strictness

An unknown key is an error rather than something quietly ignored, because a
typo in a committed file should fail on the first machine that reads it rather
than do nothing on all of them. The `aliases` key is planned but not yet
implemented, so it says so specifically rather than reading as a typo.
