# Experiment: nginx Routing and Projection Safety

## Status

- State: passed
- Date: 2026-09-05
- nginx tested: 1.29-alpine from the official image
- Arch package targeted: `nginx` 1.30.4, built `--with-http_v3_module`
- Records the evidence [ADR 0011](../../docs/adr/0011-nginx-http-server.md)
  requires, against the bar [experiment 0.5](../phase-0/caddy/README.md) set
  for Caddy

## Method

`probe.py` renders a real projection through `paddock.web.WebProjector` and
runs the projector's own `validate` against real nginx, then serves the
promoted generation and exercises it over HTTP.

The host needs no nginx. podman is already a Paddock dependency, so nginx runs
from the official image with a throwaway state tree bind-mounted at the same
absolute path it would occupy on a real machine. That keeps the probe runnable
in CI and on a machine that has not installed the package.

```bash
PYTHONPATH=src python experiments/nginx/probe.py
```

The fixture deliberately includes a project path with a space, `my blog`,
because nginx quoting is the part most likely to be wrong and its failure mode
is silent.

Only one thing is changed from a real projection: the listen address is widened
from `127.0.0.1` to `0.0.0.0`, because a container's own loopback is not where
podman forwards a published port. Every other byte is what a machine gets.

## Result

35 of 35 checks passed.

Reproducing experiment 0.5's claims:

- A static file bypassed PHP and returned its own body.
- `/` and `/articles/1` both reached the PHP location and returned 502 with no
  FPM upstream present, which is the front-controller fallback working.
- The upstream socket appeared in the error log as
  `/run/paddock/php/8.4/fpm.sock`, proving the whole `fastcgi_pass` argument is
  quoted rather than the path alone. The earlier form, `unix:"…"`, parsed
  without complaint and carried the quote characters into the path, because
  nginx only treats a quote as a delimiter at the start of a token.
- Per-site access logs are parseable JSON, one file per site.
- An invalid site block was rejected by `nginx -t`, naming the offending
  directive; the promoted generation kept serving and the rejected tree was
  removed from disk.
- `render` created nothing, so rendering for inspection has no side effects.
- A zero-site projection validated and promoted, which is what a fresh
  `paddock setup` renders before anything is linked. A glob include
  tolerates zero matches; a literal include of a missing file would not.

The cases Caddy never had to answer:

- An unknown host reached the catch-all and returned the explanatory 404
  rather than a linked site's content.
- A secured site returned `301` to `https://shop.test/` on the plaintext port,
  where Caddy served no plaintext listener at all.
- `/.env` returned 403 rather than its contents.
- `/missing.php/x.php` did not escape the PHP extension.
- A secured site with a Reverb proxy and a real mkcert-style leaf validated;
  `nginx -t` reads certificates, so an unreadable key now fails at validation
  rather than at reload.

Project types, one fixture per driver, laid out with only files a repository
commits so detection is exercised rather than asserted:

- All six fixtures were detected as their own driver.
- Laravel and Statamic served static files from `public/` and routed unknown
  paths to `index.php`; Symfony routed through its front controller.
- WordPress was served from the project root, and PHP under
  `wp-content/uploads` returned 403 rather than reaching FPM. That rule is
  emitted above the shared PHP location, because nginx takes the first
  matching regex.
- A flat PHP project was served from its own directory, exercising the
  document-root fallback.
- A static site served its files, and returned 403 for a `.php` file rather
  than handing back its source. `static` is the fallback for a directory
  nothing else matched, so it may well contain PHP.
- `blog` and `shop` deliberately carry no `type`, because that is what every
  record written before project types looks like. Both still read as Laravel
  served from `public/`.

Per-site fragments and the trust model of
[ADR 0012](../../docs/adr/0012-per-site-web-configuration.md):

- A user fragment was applied with no trust step, returning its own response.
- Two projects shipped an identical fragment, one trusted and one not. The
  trusted one's location answered; the untrusted one's did not exist as far as
  nginx was concerned. That is the security claim of the whole model, tested
  rather than asserted.
- A broken user fragment was rejected by `nginx -t`, with the error naming the
  file its author edited rather than a generated one, and the promoted
  generation kept serving.

HTTP/3:

- UDP 443 was bound for QUIC, and the `reuseport` that owns the socket sits on
  the catch-all, which is the only server block that never moves.
- A secured site completed a TLS handshake and reached its PHP handler, over
  HTTP/2, and advertised `Alt-Svc: h3=":443"`. Without that header nothing
  would ever attempt h3.
- An unlinked host was still refused at the handshake with the QUIC listener
  present, so adding it did not weaken the catch-all.

## What the container could not tell us

The image runs nginx as root and compiles its temporary paths under
`/var/cache/nginx`; Arch's build uses `/var/lib/nginx` and the unit runs as the
desktop user under `ProtectSystem=strict`. So the harness happily created
directories a real installation cannot, and `paddock setup` failed on a machine
where every check here passed — first on `uwsgi_temp_path`, then on the
compiled-in `access_log`.

The probe now reads the temporary-path prefixes out of `nginx -V` and bind
mounts each of them read-only, which reproduces the constraint the unit
imposes. Reintroducing either bug fails the run. `tests/test_web.py` also
asserts the whole class from the generated text alone, so it is caught without
a container: every `*_temp_path`, plus `pid`, `error_log` and `lock_file`, must
name a path inside Paddock state, and the http-level `access_log` must be off.

The generated tree has since been validated by the host's own Arch nginx
1.30.4, and run by it as the desktop user on unprivileged ports: static files,
dotfile denial, the catch-all, the plaintext-to-HTTPS redirect, `Alt-Svc`, and
per-site JSON logs all behaved, with a site on a stopped runtime returning 502
while a site on the live PHP 8.5 master reached it through the generated
FastCGI parameters.

## Out of scope

This probe covers the generated configuration and nginx's behavior on it. It
does not cover the systemd unit, the `CAP_NET_BIND_SERVICE` boundary,
`check-ports`, a real PHP-FPM upstream, or a reload under sustained load
against the installed stack. Those remain with
[`tests/acceptance/run.sh`](../../tests/acceptance/run.sh) on a machine with
the package installed, which is where the reload-under-load claim still needs
to be reproduced before ADR 0011 moves past "accepted for implementation".
