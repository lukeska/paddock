#!/usr/bin/env python3
"""Exercise Paddock's nginx projection against real nginx.

Run from the repository root:

    PYTHONPATH=src python experiments/nginx/probe.py

The host needs no nginx. podman is already a Paddock dependency, so the
projector's own `validate` and the serving checks run against the official
nginx image with a throwaway state tree bind-mounted at the same absolute path
it would occupy on a real machine. That keeps the probe runnable in CI and on a
developer machine that has not installed the package.

What this cannot cover, and what `tests/acceptance/run.sh` still owns: the
systemd unit, the capability boundary, `check-ports`, a real PHP-FPM upstream,
and reload under load against the installed stack.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import paddock.web

# ADR 0003 binds loopback only. Inside a container that is not where podman
# forwards a published port, so the probe widens the address. Every other byte
# of the projection is exactly what a real machine gets.
paddock.web.ADDRESS = "0.0.0.0"

from paddock import drivers, siteconfig  # noqa: E402
from paddock.paths import Paths  # noqa: E402
from paddock.web import WebProjector, WebError  # noqa: E402

IMAGE = "docker.io/library/nginx:1.29-alpine"
PORT = 18080
TLS_PORT = 18443
SOCKET = "/run/paddock/php/8.4/fpm.sock"

results: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f": {detail}" if detail else ""))


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="paddock-nginx-", dir="/tmp"))
    home = base / "home"
    # A space in the project path, because nginx quoting is the part most
    # likely to be wrong and the failure is silent.
    blog = home / "Code/my blog"
    shop = home / "Paddock/shop"
    for project in (blog, shop):
        (project / "public").mkdir(parents=True)
        (project / "public/index.php").write_text("<?php echo 'php';\n")
        (project / "public/fixture.txt").write_text("static ok\n")
        (project / "public/.env").write_text("APP_KEY=secret\n")
    for name in (".config", ".local/share", ".local/state", ".cache"):
        (home / name).mkdir(parents=True, exist_ok=True)

    # One project per driver, using only files a repository commits, so
    # detection is exercised rather than asserted.
    fixtures = {
        "laravel": ("artisan", "public/index.php", "public/fixture.txt"),
        "statamic": ("artisan", "please", "public/index.php"),
        "symfony": ("bin/console", "public/index.php"),
        "wordpress": ("wp-config.php", "index.php", "wp-content/uploads/evil.php"),
        "php": ("index.php",),
        "static": ("index.html", "tool.php"),
    }
    # These two are Laravel projects that additionally ship a fragment; they
    # are not new drivers, so they are kept out of the detection assertion.
    shipping = ("trusted", "pending")
    for label, files in fixtures.items():
        for name in files:
            path = home / "Drivers" / label / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "hello\n" if name.endswith((".html", ".txt")) else "<?php echo 'php';\n"
            )

    # Two projects that ship an nginx fragment. Each fragment adds a
    # location naming itself, so a request proves whether it was applied.
    for label in ("trusted", "pending"):
        project = home / "Drivers" / label
        (project / "public").mkdir(parents=True, exist_ok=True)
        (project / "artisan").write_text("php\n")
        (project / "public/index.php").write_text("<?php\n")
        (project / ".paddock").mkdir(exist_ok=True)
        (project / ".paddock/nginx.conf").write_text(
            f"location = /shipped {{ default_type text/plain; return 200 '{label}'; }}\n"
        )

    paths = Paths.from_environment({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local/share"),
        "XDG_STATE_HOME": str(home / ".local/state"),
        "XDG_CACHE_HOME": str(home / ".cache"),
    })

    # A real leaf, so nginx parses a certificate rather than skipping the
    # directive. `nginx -t` reads these, which `caddy validate` did not.
    pki = paths.data / "pki/sites/shop"
    pki.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-noenc",
        "-keyout", str(pki / "private-key.pem"),
        "-out", str(pki / "certificate.pem"),
        "-days", "2", "-subj", "/CN=shop.test",
    ], check=True, capture_output=True)

    # nginx creates a temporary directory for every proxying module compiled
    # in, at startup and during `nginx -t`, whether or not the module is
    # configured. On a real machine the unit runs as the desktop user under
    # ProtectSystem=strict, so any of those left at its built-in /var/lib/nginx
    # default fails with EACCES — which is exactly what shipped, because the
    # image runs as root and the mkdir simply succeeded. Mounting the prefix
    # read-only reproduces the constraint the unit imposes.
    # The prefix differs per build — Arch compiles these under /var/lib/nginx,
    # this image under /var/cache/nginx — so it is read out of the binary
    # rather than assumed, and every one of them is sealed.
    prefixes = sorted({
        argument.split("=", 1)[1].rsplit("/", 1)[0]
        for argument in subprocess.run(
            ["podman", "run", "--rm", IMAGE, "nginx", "-V"],
            text=True, capture_output=True, check=False,
        ).stderr.split()
        if argument.startswith("--http-") and "temp-path=" in argument
    })
    sealed = base / "sealed"
    sealed.mkdir()
    mounts = ["-v", f"{base}:{base}"]
    for prefix in prefixes:
        mounts += ["-v", f"{sealed}:{prefix}:ro"]

    def runner(command, **_kwargs):
        return subprocess.run(
            ["podman", "run", "--rm", *mounts, IMAGE, *command],
            text=True, capture_output=True, check=False,
        )

    projector = WebProjector(paths, runner)
    # blog and shop deliberately carry no `type`, because that is what every
    # record written before drivers existed looks like and it must still read
    # as Laravel served from public/.
    sites = {
        "blog": {"name": "blog", "root": str(blog), "php": "8.4", "secured": False},
        "shop": {"name": "shop", "root": str(shop), "php": "8.5",
                 "secured": True, "reverb": {"port": 8081}},
    }
    # A user fragment, which needs no trust because the person running
    # Paddock wrote it.
    user_fragment = siteconfig.user_path(paths, "laravel")
    user_fragment.parent.mkdir(parents=True, exist_ok=True)
    user_fragment.write_text(
        "location = /mine { default_type text/plain; return 200 'mine'; }\n"
    )

    detected: dict[str, str] = {}
    for label in fixtures:
        root = home / "Drivers" / label
        driver = drivers.detect(root)
        detected[label] = driver.name
        sites[label] = {
            "name": label, "root": str(root), "php": "8.4", "secured": False,
            "type": driver.name,
            "document_root": drivers.document_root(root, driver),
        }
    check(detected == {label: label for label in fixtures},
          "every fixture is detected as its own driver", str(detected))

    # One declared and trusted, one declared and never reviewed. Identical in
    # every other way, so the only difference is the trust.
    for label in ("trusted", "pending"):
        fragment = home / "Drivers" / label / ".paddock/nginx.conf"
        declaration = {"path": ".paddock/nginx.conf"}
        if label == "trusted":
            declaration["sha256"] = siteconfig.digest(fragment)
        sites[label] = {
            "name": label, "root": str(home / "Drivers" / label), "php": "8.4",
            "secured": False, "type": "laravel", "document_root": "public",
            "nginx": declaration,
        }

    # The first thing a fresh install projects. A glob include tolerates zero
    # matches where a literal include would be a hard error, so this is the
    # case that would break `paddock setup` outright.
    empty = projector.render({})
    try:
        projector.validate(empty)
        check(True, "a zero-site projection validates", ", ".join(sorted(empty.files)))
    except WebError as error:
        check(False, "a zero-site projection validates", str(error))

    candidate = projector.render(sites)
    check(not candidate.directory.exists(), "render is pure",
          "reserved a generation without creating it")
    try:
        projector.validate(candidate)
        check(True, "nginx accepts the projection",
              "one plain site, one secured site with a Reverb proxy")
    except WebError as error:
        check(False, "nginx accepts the projection", str(error))
        return report()
    projector.write(candidate)
    promoted = projector.current.resolve()

    # A broken site block must be caught without disturbing what is serving.
    broken = projector.render(sites)
    broken = type(broken)(broken.directory, {
        **broken.files,
        "sites/blog.conf": "server {\n\tlisten 0.0.0.0:80;\n\tnot_a_directive on;\n}\n",
    })
    try:
        projector.validate(broken)
        check(False, "invalid site block is rejected", "it was accepted")
    except WebError as error:
        check("not_a_directive" in str(error), "invalid site block is rejected",
              str(error).splitlines()[-1].strip())
    check(projector.current.resolve() == promoted,
          "promoted generation survives a rejection")
    check(not broken.directory.exists(), "rejected generation is removed")

    # A fragment is included by path, so nginx judges its contents too. A
    # mistake has to be caught by the render that noticed it rather than by
    # whatever unrelated operation reloads next.
    user_fragment.write_text("not_a_directive on;\n")
    try:
        projector.validate(projector.render(sites))
        check(False, "a broken user fragment is rejected", "it was accepted")
    except WebError as error:
        check(str(user_fragment) in str(error),
              "a broken user fragment is rejected, naming the file its author edited",
              str(error).splitlines()[0].strip()[:96])
    check(projector.current.resolve() == promoted,
          "a broken fragment leaves the promoted generation serving")
    user_fragment.write_text(
        "location = /mine { default_type text/plain; return 200 'mine'; }\n"
    )
    candidate = projector.render(sites)
    projector.validate(candidate)
    projector.write(candidate)
    promoted = projector.current.resolve()

    # mkdtemp gives 0700 and the image's worker drops to an unprivileged user,
    # so without this the worker cannot traverse the project and try_files
    # reads every static file as missing. On a real machine nginx runs as the
    # project's owner, so this models the deployed permissions rather than
    # papering over a configuration fault.
    subprocess.run(["chmod", "-R", "a+rX", str(home / "Code"),
                    str(home / "Paddock"), str(home / "Drivers")], check=True)
    subprocess.run(["chmod", "-R", "a+rX", str(paths.config)], check=True)
    subprocess.run(["chmod", "a+rx", str(base), str(home)], check=True)

    container = subprocess.run([
        "podman", "run", "-d", "--rm", *mounts,
        "-p", f"{PORT}:80", "-p", f"{TLS_PORT}:443", IMAGE,
        "nginx", "-c", str(promoted / "nginx.conf"), "-p", str(promoted),
    ], text=True, capture_output=True, check=True).stdout.strip()

    try:
        for _ in range(50):
            try:
                get("/fixture.txt", "blog.test")
                break
            except OSError:
                time.sleep(0.1)

        status, body = get("/fixture.txt", "blog.test")
        check(status == 200 and body == "static ok",
              "static file bypasses PHP", f"{status} {body!r}")

        status, _ = get("/", "blog.test")
        check(status == 502, "index routes to the FPM socket", str(status))

        status, _ = get("/articles/1", "blog.test")
        check(status == 502, "front-controller route falls through to index.php",
              str(status))

        status, _ = get("/.env", "blog.test")
        check(status == 403, "dotfile is denied", str(status))

        status, _ = get("/missing.php/x.php", "blog.test")
        check(status in (403, 404, 502),
              "path-info request cannot escape the PHP extension", str(status))

        status, body = get("/", "nope.test")
        check(status == 404 and "paddock link" in body,
              "unknown host reaches the catch-all, not a linked site",
              f"{status} {body!r}")

        status, headers = head("/", "shop.test")
        check(status == 301 and headers.get("Location") == "https://shop.test/",
              "secured site redirects plaintext to HTTPS",
              f"{status} {headers.get('Location')!r}")

        status, body = get("/fixture.txt", "laravel.test")
        check(status == 200 and body == "hello",
              "laravel serves static files from public/", f"{status} {body!r}")

        status, _ = get("/dashboard", "laravel.test")
        check(status == 502, "laravel routes unknown paths to index.php", str(status))

        status, _ = get("/api/users", "symfony.test")
        check(status == 502, "symfony routes through its front controller", str(status))

        status, _ = get("/", "statamic.test")
        check(status == 502, "statamic serves from public/ like Laravel", str(status))

        status, _ = get("/", "wordpress.test")
        check(status == 502, "wordpress serves from the project root", str(status))

        status, _ = get("/wp-content/uploads/evil.php", "wordpress.test")
        check(status == 403,
              "wordpress refuses PHP under wp-content/uploads", str(status))

        status, _ = get("/", "php.test")
        check(status == 502,
              "a flat PHP project is served from its own directory", str(status))

        status, body = get("/index.html", "static.test")
        check(status == 200 and body == "hello",
              "a static site serves its files", f"{status} {body!r}")

        status, body = get("/tool.php", "static.test")
        check(status == 403 and "php" not in body,
              "a static site neither runs PHP nor leaks its source",
              f"{status} {body[:40]!r}")

        status, body = get("/mine", "laravel.test")
        check(status == 200 and body == "mine",
              "a user fragment is applied without any trust step",
              f"{status} {body!r}")

        status, body = get("/shipped", "trusted.test")
        check(status == 200 and body == "trusted",
              "a trusted project fragment is applied", f"{status} {body!r}")

        # The security claim of the whole trust model: an identical fragment
        # that nobody reviewed contributes nothing at all.
        status, _ = get("/shipped", "pending.test")
        check(status == 502,
              "an untrusted project fragment contributes nothing", str(status))

        # HTTP/3. QUIC needs TLS, so only the secured site offers it, and the
        # `reuseport` that owns the UDP socket is anchored on the catch-all
        # because nginx allows exactly one per address and port.
        listeners = subprocess.run(
            ["podman", "exec", container, "sh", "-c",
             "netstat -lnu 2>/dev/null | grep ':443' || true"],
            text=True, capture_output=True, check=False,
        ).stdout
        check(":443" in listeners, "UDP 443 is bound for QUIC",
              listeners.strip().splitlines()[0].strip() if listeners.strip() else "not bound")

        headers = https_headers("/", "shop.test")
        first = headers.splitlines()[0].strip() if headers.strip() else ""
        # 502 is the right answer with no FPM upstream: it proves nginx
        # completed the handshake and reached the PHP handler.
        check(first.startswith(("HTTP/1.1 502", "HTTP/2 502")),
              "a secured site serves over TLS and reaches its PHP handler",
              first or "no response")
        # Header names are lowercased over HTTP/2, so this cannot be matched
        # case-sensitively.
        advertised = next(
            (line.strip() for line in headers.splitlines()
             if line.lower().startswith("alt-svc:")),
            "",
        )
        check('h3=":443"' in advertised,
              "the secured site advertises h3, or nothing would ever use it",
              advertised or "no Alt-Svc header")

        rejected = https_headers("/", "nope.test")
        check(rejected.strip() == "",
              "an unlinked host is still refused at the TLS handshake",
              rejected.strip()[:60] or "handshake refused")

        log = (paths.state / "logs/nginx.log").read_text()
        check(SOCKET in log, "upstream socket path is unquoted correctly",
              SOCKET if SOCKET in log else "not found in error log")

        access = (paths.state / "logs/sites/blog.json").read_text().splitlines()
        record = json.loads(access[0])
        check(record["host"] == "blog.test" and isinstance(record["status"], int),
              "per-site access log is parseable JSON", access[0][:72] + "...")
        check(not (paths.state / "logs/sites/shop.json").exists()
              or "blog" not in (paths.state / "logs/sites/shop.json").read_text(),
              "each site logs to its own file")
    finally:
        subprocess.run(["podman", "stop", "-t", "1", container], capture_output=True)

    return report()


def get(path: str, host: str) -> tuple[int, str]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", headers={"Host": host}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as reply:
            return reply.status, reply.read().decode(errors="replace").strip()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors="replace").strip()


def https_headers(path: str, host: str) -> str:
    """Response headers over TLS, with SNI set to the host being tested.

    curl rather than urllib because the server name has to reach nginx as SNI:
    the catch-all rejects any handshake it does not recognize, so a request to
    127.0.0.1 with only a Host header never gets far enough to answer.
    """
    result = subprocess.run(
        ["curl", "-sk", "-D-", "-o", "/dev/null", "--max-time", "10",
         "--resolve", f"{host}:{TLS_PORT}:127.0.0.1",
         f"https://{host}:{TLS_PORT}{path}"],
        text=True, capture_output=True, check=False,
    )
    return result.stdout


def head(path: str, host: str) -> tuple[int, dict[str, str]]:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", headers={"Host": host}, method="HEAD"
    )
    try:
        with opener.open(request, timeout=5) as reply:
            return reply.status, dict(reply.headers)
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers)


def report() -> int:
    passed = sum(1 for ok, _, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
