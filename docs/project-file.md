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

services:             # optional
  postgres:
    version: "17"     # optional; defaults to the catalog version
    port: 5432        # optional; defaults to the catalog port
  redis:              # an empty body means "the defaults"
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

`paddock init` records the declaration, reports it with `!`, and exits 1 until
it is trusted, the same as anything else it declines to impose. Trust is
recorded as the file's SHA-256, so an edit or a pull that changes it withdraws
trust automatically — nobody has to notice.

Trust is per machine. Cloning the same project elsewhere means reviewing it
there.

Your own directives need no trust, because you wrote them:

```
paddock config edit         # your own fragment for this site
paddock reload              # apply a fragment you edited elsewhere
```

Both are included at the end of the site's server block, the project's first
and yours last, so a plain directive overrides the generated one above it. A
`location` is different: nginx prefers the longest matching prefix and only
then tries regular expressions in order, so to beat one of Paddock's generated
regex locations use `^~` or an exact `=` match. Everything is validated with
`nginx -t` before it is promoted, so a mistake is reported against your file
and the site keeps serving what it served before.

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
workers need no published port or web-server route.

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
