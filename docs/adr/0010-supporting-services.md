# ADR 0010: Supporting Services

- Status: accepted for implementation
- Date: 2026-08-20
- Extends ADR 0005 to services Paddock does not build; see the amendment there.

## Context

A Laravel project needs more than PHP. `CACHE_STORE=redis` in a stock `.env`
fails with `RedisException: Connection refused` until something listens on
127.0.0.1:6379. The phpredis extension is already in the Paddock runtime
baseline, so the gap is the server, not the client.

Unlike PHP, Paddock has no reason to build these servers. They are widely
packaged, their versions matter to users who mirror production, and their data
is precious in a way a PHP runtime's is not.

Arch no longer ships `redis`; `extra/valkey` provides it. The tested Omarchy
machine has Docker 29.7.2 and no Podman.

## Decision

Supporting services run as **rootless Podman containers owned by the user's
systemd manager**, one shared instance per service.

- `paddock service add NAME` writes `~/.config/systemd/user/paddock-service-<name>.service`
  and needs no privilege at all. Adding PostgreSQL later requires no
  privileged action either.
- `paddock setup` enables lingering for the desktop user, disclosed in the
  change list it already prints for confirmation. That is what makes a user
  unit start at boot and survive logout.
- `paddock service start` enables as well as starts, so a configured service
  returns after a reboot rather than needing a manual start each time.
- `Type=notify` with `--sdnotify=conmon` is the readiness gate ADR 0005
  requires. Podman would otherwise report the unit started as soon as it
  forked.
- Redis defaults to one instance on 6379 with the image pinned to a
  registry-qualified `docker.io/library/redis:8`, so a stock Laravel `.env`
  needs no edit. Every planned service publishes above 1024, so rootless never
  needs a privileged bind.
- Ports are published on loopback only. Paddock never opens a routable port.
- Data lives in a named podman volume that **outlives `paddock service
  remove`** unless `--delete-data` is given.
- `podman` is a hard `depends`, matching caddy, dnsmasq, mkcert, and nss.
  Paddock never installs it itself: ADR 0007 confines privileged changes to
  setup and uninstall, and driving pacman from a routine command would contend
  for the database lock and risk a partial upgrade.

## Why rootless removed a privilege boundary

The first implementation used a root-owned templated unit plus
`/usr/lib/paddock/service-launcher`, a packaged helper that rebuilt every
podman argument from a validated configuration file. That helper existed
because a root-owned unit reading a user-writable config would turn that file
into root's argument list, putting `--privileged` or `--volume /:/host` one
edit away.

A user-owned unit has no such gap: the account that writes the unit is the
account that runs it, and it already has that authority. The launcher, its
validation, and its attack tests were deleted rather than hardened. Declining
to write privileged code is worth more than reviewing it carefully.

Rootless also keeps the service consistent with the sandbox the same machine
applies to PHP, where ADR 0005's amendment hides `~/.ssh`, `~/.gnupg`, and the
CA key from php-fpm. A container escape lands on an unprivileged account
instead of root.

## Rejected alternatives

**Native systemd service running valkey.** Recommended during design and
rejected by the maintainer in favour of containers. Lighter (4.3 MB, no
engine) and consistent with dnsmasq and Caddy, but limited to whatever version
Arch ships, which is the wrong tradeoff for services that mirror production.

**Docker.** Already installed on the tested machine and socket-activated, so
containers would not reliably return after a reboot until something touched
the socket. Podman is daemonless, so the unit *is* the container.

**Rootful Podman in a system unit.** Implemented first, then replaced. It kept
one manager and needed no lingering, but it required the launcher described
above and ran containers as root. `lerd`, a comparable Podman-based
environment, also chose rootless with `loginctl enable-linger`, which is
evidence the model is workable rather than merely appealing.

**Per-project instances.** Every project would need its own `REDIS_PORT`, and
the port is not predictable before the service exists. Named instances remain
possible later.

**Writing the project's `.env`.** Deliberately not done. The file is
user-owned, and a shared instance on the default port means a stock Laravel
configuration already works.

## Consequences

- Lingering is user-global: every enabled user unit now survives logout, not
  only Paddock's. This is disclosed at setup and undone at uninstall, but only
  when Paddock was the one that enabled it, recorded as
  `linger_enabled_by_paddock` in the installation record.
- `paddock start`, `stop`, `status`, and `doctor` span two managers. Nothing is
  ordered between them, because Caddy never depends on Redis and PHP connects
  at request time.
- `podman` adds roughly 98 MB installed, including netavark, aardvark-dns,
  crun, conmon, catatonit, containers-common, and passt.
- Two container engines can coexist on a machine that already has Docker; the
  maintainer accepted this.
- Image tags, not digests, are pinned. Runtime archives are checksum- and
  attestation-verified; container images are not yet held to that standard.
- The catalog now holds Redis, MySQL and PostgreSQL. Adding one needs no
  privileged action and no new architecture: an entry naming the image, port,
  data path, environment, connection settings and readiness probe. MariaDB and
  the `paddock.yml` reconciliation in the roadmap build on the same unit and
  state record.
- Databases run without a password, which is the local-development convention
  Herd, Valet and DBngin follow, and is what an unedited Laravel `.env`
  expects. It is defensible only because every port is published on loopback
  alone. `paddock service add` prints the settings rather than writing the
  `.env`, which stays the user's file.
- Readiness cannot be measured from the host. Podman binds a published port as
  soon as the container starts, so the forwarder accepts while the database is
  still initialising: a cold Postgres reported ready in 0.6s and refused the
  next query. The probe therefore runs inside the container and speaks the
  service's protocol over TCP, since both entrypoints run a temporary
  socket-only server during first-run initialisation.
