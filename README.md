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
- Supporting services (Redis, MySQL, PostgreSQL, Mailpit, Meilisearch, RustFS) run as rootless containers
  in user systemd units, published on loopback only.
- A committed `paddock.yml` describes what a project needs; `paddock init`
  converges the machine towards it, idempotently.
- The optional Omarchy plugin shows health, PHP runtimes, services, and
  linked sites, reading only `paddock report`, and owns no canonical state.

The architectural decisions are recorded in [`docs/adr`](docs/adr).

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
