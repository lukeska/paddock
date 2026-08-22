# Paddock

[![CI](https://github.com/lukeska/paddock/actions/workflows/ci.yml/badge.svg)](https://github.com/lukeska/paddock/actions/workflows/ci.yml)

Paddock is a native local Laravel development environment for
[Omarchy](https://omarchy.org/). It provides managed PHP runtimes, `.test`
domains, HTTPS, Caddy routing, and project-specific PHP selection through one
CLI, with an optional Omarchy status widget.

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
- Caddy serves linked projects over HTTP or locally trusted HTTPS.
- Each managed PHP minor runs in an isolated PHP-FPM service.
- The optional Omarchy plugin shows health, PHP runtimes, services, and
  linked sites, reading only `paddock report`, and owns no canonical state.

The architectural decisions are recorded in [`docs/adr`](docs/adr).

## Development

Run the unit suite:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

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
