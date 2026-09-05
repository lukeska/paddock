# ADR 0011: nginx as the HTTP Server

- Status: accepted for implementation
- Date: 2026-09-05
- Supersedes: [ADR 0003](0003-http-server.md)
- Amends: [ADR 0002](0002-local-tls.md), TLS serving clauses only

## Context

ADR 0003 selected Caddy and it worked: experiment 0.5 recorded validated
reloads under load, invalid-directive rejection with last-known-good preserved,
and per-site 502 isolation. Nothing about that decision failed.

What changed is scope. Paddock will serve projects that are not Laravel, and
each site will be able to carry its own web-server configuration. Both goals are
cheaper in nginx:

- Framework rules for WordPress, Symfony, Statamic, and Magento exist as
  known-good nginx blocks. In Caddy each would be a translation, and rewrite
  translations are where subtle bugs live.
- Laravel Herd exposes a per-site `.conf` users are told to edit, and Lerd ships
  per-framework nginx rules. The snippets a user already has, and the ones they
  will find when they search, are nginx.
- Production parity: Forge, Ploi, and plain VPS deployments are nginx, so header,
  redirect, and rewrite behavior transfers in both directions.

ADR 0003 is worth reading for what it did *not* decide. Its rejected
alternatives are all about how to launch Caddy — a file capability, the packaged
unit, a root process, packet forwarding, socket activation. It never compared
another server, so there is no recorded decision on the merits to overturn.

## Decision

Use nginx as the sole Paddock HTTP server. Do not keep Caddy available as an
alternative engine, and do not introduce a setting that selects one.

The privilege boundary of ADR 0003 is carried over unchanged and remains the
governing constraint:

- A fixed, root-owned unit launches the server as the desktop user.
- The process receives only `CAP_NET_BIND_SERVICE`, through the unit's ambient
  and bounding sets. No capability is set on `/usr/bin/nginx`.
- PID 1 grants the low-port capability and never interprets project paths or
  the mutable generated site map.
- Loopback only: `127.0.0.1:80/tcp` and `127.0.0.1:443/tcp`.
- The root-owned `check-ports` preflight still runs before start and still
  refuses to kill, disable, or reconfigure an existing listener.

The unit is named `paddock-web.service`, for the role rather than the engine,
following `paddock-dns.service`, which runs dnsmasq. For the same reason the
projector is `paddock.web.WebProjector` and the dashboard row is keyed `web`,
with the engine named in its detail line and in `paddock report`'s new `web`
block. Nothing outside `web.py` and one unit body names nginx.

## Unit differences from ADR 0003

- `Type=simple` with `daemon off;` replaces `Type=notify`, because nginx has no
  `sd_notify`. Nothing in `paddock.target` orders itself after web readiness —
  only `paddock-dns-route.service` is upstream, and the FPM units gate
  themselves with `wait-for-socket` — so no ordering guarantee is lost. The
  foreground process keeps systemd tracking the real main process instead of a
  forked master behind a PID file.
- Two `ExecReload` lines run in sequence: `nginx -t`, then `SIGHUP`. A
  configuration that fails the test never reaches the signal, and the running
  generation keeps serving. This is the same last-known-good property Caddy
  provided, and in both cases it lives in the running process, not on disk.
- `KillSignal=SIGQUIT`, which is nginx's graceful shutdown signal.
- `ReadWritePaths` must cover the pid file, error log, per-site access logs, and
  the three request temporary directories, all relocated under Paddock state
  because `ProtectSystem=strict` would otherwise deny them.
- The Caddy admin endpoint on `127.0.0.1:20195` is gone. Reload is a signal, so
  no management socket listens at all, which closes the open question experiment
  0.5 recorded about moving that endpoint to a Unix socket.

## Generated configuration

One Caddyfile becomes a tree, which introduces an atomicity problem the single
file did not have: a partially written tree followed by any reload would load a
half-generated configuration. Each projection is therefore written to
`nginx/generations/<counter>/` and promoted by renaming the `nginx/current`
symlink, which is atomic. Three generations are kept for rollback and diffing.

`nginx.conf` lives inside its generation and includes its site blocks with a
relative path, so the same rendered bytes validate in a staging directory and
run under `current/`. Only that include is relative; pid, logs, and temporary
paths are absolute, because they are shared across generations.

Site blocks are included with a glob. A glob include tolerates zero sites, which
matches the previous behavior where an empty Caddyfile was valid; a literal
include of a missing file would be a hard error.

`render` is pure: it reserves a generation directory but creates nothing, so
rendering for inspection has no side effects.

## Behavior this decision adds

Three things nginx requires that Caddy handled implicitly, each a regression if
omitted:

- **A catch-all server.** Without `default_server`, the first server block in
  the tree silently answers every unmatched host, serving one project's files
  under another's name. The generated catch-all returns an explanatory 404 on
  port 80, and on 443 uses `ssl_reject_handshake on` so an unlinked host is
  refused rather than shown some other site's certificate. That needs no
  certificate of its own.
- **Dotfile denial.** Caddy's `php_fastcgi` refused dotfiles; nginx would serve
  `.env` as plain text. Every site block denies `/\.(?!well-known)`.
- **Scheme propagation.** Caddy's `php_fastcgi` set `HTTPS` implicitly. nginx
  does not, so a `map $scheme` supplies it and a secured site stops emitting
  `http://` URLs.

One deliberate improvement: a secured site now also answers on port 80 and
redirects, where Caddy served no plaintext listener for it at all.

## HTTP/3

Restored, after the rest of this record landed. ADR 0003 bound UDP 443 by
default and the previous server offered HTTP/3 without being asked, so leaving
it out would have been a regression rather than a simplification. It was
sequenced last because it is the only part of the migration that touches the
port boundary.

Arch's nginx is built `--with-http_v3_module`. A secured site gets
`listen 127.0.0.1:443 quic` beside its TLS listener, `http3 on`, and an
`Alt-Svc` header, without which nothing would ever try h3. A plaintext site
gets none of it, because QUIC requires TLS.

`reuseport` may appear only once for an address and port in a whole
configuration, so it is anchored on the catch-all server. That block always
exists and never moves as sites are linked, secured, or removed — anchoring it
on a site would mean the socket owner shifting between reloads, and a moment
with two or none. A test asserts the single occurrence for several site counts,
because nginx rejecting a duplicate makes this a constraint rather than a
preference.

This reintroduces the hazard ADR 0003 made its preflight mandatory for: with
`SO_REUSEPORT` a second server shares the socket silently instead of failing to
bind. `check-ports` therefore still checks UDP 443, and still refuses to start
rather than resolving a conflict itself.

## Consequences

- `depends` gains `nginx` and loses `caddy`.
- ADR 0002 is unchanged in substance: Paddock still owns a mkcert CA and the
  server is still handed leaf files it does not own. Only the directive changes,
  from `tls <cert> <key>` to `ssl_certificate`/`ssl_certificate_key`. The hazard
  ADR 0002 recorded — Caddy's internal issuer attempting trust-store
  installation as a side effect of starting the server — no longer exists,
  because nginx has no CA, so the defensive `auto_https off` is gone with it.
- `nginx -t` validates more than syntax: it opens the error log and reads every
  certificate named, so an unreadable leaf key now fails at validation rather
  than at reload. The tree must therefore exist on disk before the check runs.
- A machine upgrading from a Caddy release has `paddock-caddy.service` installed
  by a previous integration, not by pacman. Install stops and deletes it before
  writing the new unit, and removal cleans it, because two servers cannot share
  the loopback web ports.
- nginx interpolates `$` inside quoted strings with no escape for it, so a
  project path containing `$` cannot be expressed and is refused by name at
  render time rather than surfacing as an unknown-variable error in generated
  configuration the user never wrote.
- `paddock report` gains a `web` block. It deliberately costs no fork: the unit
  state already arrives in the batched `is-active` reply, and the engine version
  would add a third subprocess call to every bar tick for decoration.

## Evidence

Experiment 0.5's bar, reproduced for nginx, plus the cases Caddy never had to
answer and the ones the later phases added. 35 checks against real nginx, in
[`experiments/nginx`](../../experiments/nginx/README.md).

Still outstanding on an installed machine, and owned by
[`tests/acceptance/run.sh`](../../tests/acceptance/run.sh): the systemd unit,
the capability boundary, `check-ports`, a real PHP-FPM upstream, and a reload
under sustained load.
