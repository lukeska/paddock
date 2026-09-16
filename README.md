# Paddock

[![CI](https://github.com/lukeska/paddock/actions/workflows/ci.yml/badge.svg)](https://github.com/lukeska/paddock/actions/workflows/ci.yml)

Paddock is a native local Laravel development environment for
[Omarchy](https://omarchy.org/). It provides managed PHP runtimes, `.test`
domains, HTTPS, nginx routing, and project-specific PHP selection through one
CLI, with optional supporting services and an Omarchy status widget.

```bash
paddock help
paddock link
paddock php use 8.5
paddock secure
```

A cloned project describing itself in [`paddock.yml`](docs/project-file.md)
needs one command:

```bash
paddock init
```

## Status

Paddock is in active development and does not have a supported public release
yet. The CLI, system integration, Arch package, PHP 8.4/8.5 runtime pipeline,
and optional Omarchy plugin have passed local acceptance testing. The accepted
x86_64 PHP runtimes are available as an unsigned GitHub prerelease. Package
signing, CI attestations, and automated publication are still being completed.

## Architecture

- An Arch package owns the CLI and fixed privileged helpers.
- Per-user state follows the XDG directory conventions.
- `dnsmasq` and NetworkManager route `.test` domains locally.
- nginx serves linked projects over HTTP or locally trusted HTTPS, from a
  generated configuration tree Paddock owns.
- A project's type — Laravel, Statamic, Symfony, WordPress, plain PHP, or a
  static site — is detected from committed files and decides its document
  root and routing rules.
- Each managed PHP minor runs in an isolated PHP-FPM service.
- Supporting services (Redis, MySQL, PostgreSQL, Mailpit, Meilisearch, Typesense, RustFS) run as rootless containers
  in user systemd units, published on loopback only.
- A committed `paddock.yml` describes what a project needs; `paddock init`
  converges the machine towards it, idempotently.
- The optional Omarchy plugin shows health, PHP runtimes, services, and
  linked sites, reading only `paddock report`, and owns no canonical state.

The architectural decisions are recorded in [`docs/adr`](docs/adr).

## CLI reference

Run `paddock help` to see the command overview, `paddock help COMMAND` for
command-specific help, or `paddock --version` to print the installed version.
Arguments in square brackets are optional. Commands that forward arguments to
another program require `--` before those arguments.

### Projects and sites

| Command | Description |
| --- | --- |
| `paddock link [NAME] [--php VERSION] [--node VERSION] [--type TYPE] [--root PATH]` | Serve the current directory at `NAME.test`. The name and project type are detected when omitted. `--root` is relative to the project. |
| `paddock unlink [NAME]` | Stop serving a linked project. From inside a project, the site name may be omitted. |
| `paddock sites` | List linked sites with their type, PHP version, scheme, and project root. |
| `paddock secure [NAME]` | Issue a locally trusted certificate and switch the site to HTTPS. |
| `paddock unsecure [NAME]` | Switch the site back to plain HTTP. |
| `paddock reload` | Regenerate, validate, and reload the nginx configuration. |
| `paddock init [--dry-run]` | Apply the nearest project's `paddock.yml`, including its declared local environment. `--dry-run` reports changes without applying them. |

Supported values for `paddock link --type` are `laravel`, `php`, `statamic`,
`static`, `symfony`, and `wordpress`.

### Per-site nginx configuration

| Command | Description |
| --- | --- |
| `paddock config` | Show the current site's configuration, or summarize every site when run outside a linked project. |
| `paddock config [NAME]` | Show one site's user and project-provided nginx fragments. |
| `paddock config show [NAME]` | Explicit form of the show command. |
| `paddock config edit [NAME]` | Create if needed and open the user-owned nginx fragment, then validate and apply it. |
| `paddock config trust [NAME]` | Trust and apply the nginx fragment committed by the project. |
| `paddock config revoke [NAME]` | Stop applying the project's nginx fragment. |

The site name defaults to the linked site containing the current directory.

### Parked directories

| Command | Description |
| --- | --- |
| `paddock park [PATH]` | Watch a directory and serve each immediate child as a `.test` site. The path defaults to the current directory. |
| `paddock park --refresh` | Rescan all parked directories without adding another path. |
| `paddock paths` | List parked directories. |
| `paddock forget [PATH]` | Stop watching a parked directory without deleting its projects. The path defaults to the current directory. |

### PHP and Composer

| Command | Description |
| --- | --- |
| `paddock php list` | List installed PHP runtimes and their paths. |
| `paddock php install VERSION` | Install the published runtime for a PHP minor, such as `8.5`. |
| `paddock php remove VERSION` | Remove an installed PHP runtime. |
| `paddock php use VERSION` | Select PHP for the current project in `.paddock.json`. |
| `paddock php catalog PATH` | Install a local PHP artifact catalog as the user-level catalog override. |
| `paddock php -- ARGS` | Run PHP with the version selected for the current directory. Example: `paddock php -- -v`. |
| `paddock composer -- ARGS` | Run Composer with the selected PHP. Example: `paddock composer -- install`. |

PHP selection uses the nearest `.paddock.json` while walking up from the
current directory, then falls back to the containing linked site and finally
the configured default runtime.

### Node.js

| Command | Description |
| --- | --- |
| `paddock node list` | List installed Node.js runtimes and their paths. |
| `paddock node install VERSION` | Install a checksum-verified Node.js runtime by major version. |
| `paddock node remove VERSION` | Remove an installed Node.js runtime. |
| `paddock node use VERSION` | Select Node.js for the current project in `.paddock.json`. |
| `paddock node -- ARGS` | Run Node.js with the version selected for the current directory. Example: `paddock node -- --version`. |

After `paddock setup`, new terminal sessions also expose project-aware `php`,
`composer`, `node`, `npm`, and `npx` shims directly.

### Supporting services

| Command | Description |
| --- | --- |
| `paddock services` | List configured service instances, their state, address, and image. |
| `paddock service add TYPE [--name NAME] [--image IMAGE] [--port PORT]` | Create and start an independent service instance. |
| `paddock service start ID` | Start a service instance. |
| `paddock service stop ID` | Temporarily stop a service instance. |
| `paddock service restart ID` | Restart a service instance. |
| `paddock service logs ID` | Print the instance's journal. |
| `paddock service remove ID` | Remove the instance and permanently delete its Paddock data volume. |

`TYPE` may be `mailpit`, `meilisearch`, `mysql`, `postgres`, `redis`, `rustfs`,
or `typesense`. Use the instance ID printed by `paddock services` for lifecycle,
logs, and removal commands. `--image` accepts a registry-qualified, pinned
image when a project needs a version other than the catalog default.

### Site workers

| Command | Description |
| --- | --- |
| `paddock worker start reverb [SITE]` | Start the Laravel Reverb worker, allocating its loopback port when needed. |
| `paddock worker stop reverb [SITE]` | Stop the Reverb worker. |
| `paddock worker restart reverb [SITE]` | Restart the Reverb worker. |
| `paddock worker logs reverb [SITE]` | Print the Reverb journal. |
| `paddock worker start queue [SITE]` | Start the Laravel queue worker. |
| `paddock worker stop queue [SITE]` | Stop the queue worker. |
| `paddock worker restart queue [SITE]` | Restart the queue worker. |
| `paddock worker logs queue [SITE]` | Print the queue worker journal. |

The site defaults to the linked project containing the current directory.
Reverb is available when the project has `laravel/reverb`; queue controls are
available for detected Laravel projects.

### Paddock lifecycle and diagnostics

| Command | Description |
| --- | --- |
| `paddock tui` | Open the Omarchy-themed terminal interface. |
| `paddock status` | Show the state of Paddock's system and user services. Exits with status 3 when any required service is inactive. |
| `paddock start` | Start the system-level `paddock.target`. |
| `paddock stop` | Stop the system-level `paddock.target`. |
| `paddock restart` | Restart the system-level `paddock.target`. |
| `paddock logs [--follow]` | Print the web, PHP-FPM, and DNS journal. `--follow` continues streaming it. |
| `paddock doctor` | Check runtimes, state, sites, and generated configuration. Exits with status 1 when a check fails. |
| `paddock report` | Print the stable, versioned JSON snapshot used by scripts and the Omarchy plugin. |

### Installation and removal

| Command | Description |
| --- | --- |
| `paddock setup [--yes]` | Install Paddock's systemd, DNS, nginx, certificate, runtime, Composer, Node.js, and shell integration. `--yes` skips the confirmation prompt. |
| `paddock uninstall [--yes]` | Remove system integration while preserving projects, configuration, runtimes, logs, cache, certificates, and service data. |
| `paddock uninstall --purge [--yes]` | Also delete Paddock-owned user configuration, runtimes, Composer, logs, cache, and the private CA. Project source directories and service volumes remain. |
| `paddock uninstall --purge --delete-service-data [--yes]` | Also permanently delete the recorded supporting-service data volumes. |

`setup` and the system-integration portion of `uninstall` require privilege
escalation. `--delete-service-data` is accepted only together with `--purge`.

### Help

| Command | Description |
| --- | --- |
| `paddock help` | Show the grouped command overview. |
| `paddock help COMMAND` | Show detailed help for one top-level command. |
| `paddock --help` | Show the grouped command overview. |
| `paddock --version` | Print the installed Paddock version. |

## Development

Run the unit suite:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
go test ./...
go vet ./...
```

Python discovery includes the PTY acceptance tests for the compiled TUI. CI
provisions the Go version declared in `go.mod` and runs both suites explicitly.

Build the local Arch package:

```bash
./packaging/arch/build-local.sh
```

Build the pinned PHP runtime matrix:

```bash
./release/php/build.sh all
```

## License

Paddock is available under the [MIT License](LICENSE).
