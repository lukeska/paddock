from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from paddock.paths import Paths
from paddock.web import WebProjector


class ProjectionInvariantTests(unittest.TestCase):
    """Properties of the whole tree, rather than of one site block."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.paths = Paths.from_environment({
            "HOME": str(base / "home"),
            "XDG_CONFIG_HOME": str(base / "config"),
            "XDG_DATA_HOME": str(base / "data"),
            "XDG_STATE_HOME": str(base / "state"),
            "XDG_CACHE_HOME": str(base / "cache"),
        })
        self.projector = WebProjector(self.paths)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def render(self, secured: int = 0, plain: int = 0) -> dict[str, str]:
        sites = {}
        for index in range(secured):
            name = f"secure{index}"
            sites[name] = {
                "name": name, "root": f"/projects/{name}", "php": "8.4",
                "secured": True,
            }
        for index in range(plain):
            name = f"plain{index}"
            sites[name] = {
                "name": name, "root": f"/projects/{name}", "php": "8.4",
                "secured": False,
            }
        return dict(self.projector.render(sites).files)

    def whole(self, **counts: int) -> str:
        return "\n".join(self.render(**counts).values())

    def test_reuseport_appears_exactly_once_whatever_the_site_count(self) -> None:
        # nginx rejects a second `reuseport` for the same address and port, so
        # this is a hard constraint rather than a preference. It is anchored on
        # the catch-all because that block always exists and never moves as
        # sites are linked, secured, or removed.
        for secured, plain in ((0, 0), (1, 0), (3, 2), (0, 4)):
            with self.subTest(secured=secured, plain=plain):
                self.assertEqual(
                    1, self.whole(secured=secured, plain=plain).count("reuseport")
                )

    def test_the_reuseport_anchor_is_the_catch_all(self) -> None:
        files = self.render(secured=2)
        self.assertIn("reuseport", files["sites/000-default.conf"])
        self.assertNotIn("reuseport", files["sites/secure0.conf"])

    def test_only_a_secured_site_speaks_quic(self) -> None:
        # QUIC requires TLS, so a plaintext site has nothing to offer over it.
        files = self.render(secured=1, plain=1)
        self.assertIn("listen 127.0.0.1:443 quic;", files["sites/secure0.conf"])
        self.assertIn("http3 on;", files["sites/secure0.conf"])
        self.assertIn("Alt-Svc", files["sites/secure0.conf"])
        for absent in ("quic", "http3", "Alt-Svc"):
            self.assertNotIn(absent, files["sites/plain0.conf"])

    def test_the_catch_all_is_always_present(self) -> None:
        # Without a default server the first block in the tree answers every
        # unmatched host, serving one project under another's name.
        for secured, plain in ((0, 0), (2, 2)):
            with self.subTest(secured=secured, plain=plain):
                files = self.render(secured=secured, plain=plain)
                self.assertIn("sites/000-default.conf", files)
                self.assertIn("default_server", files["sites/000-default.conf"])
                self.assertIn("ssl_reject_handshake on;", files["sites/000-default.conf"])

    def test_every_listener_stays_on_loopback(self) -> None:
        # ADR 0003 binds loopback only, and a listen line that lost its
        # address would publish every linked project on the network.
        for line in self.whole(secured=2, plain=2).splitlines():
            if line.strip().startswith("listen "):
                self.assertIn("127.0.0.1:", line, line)


if __name__ == "__main__":
    unittest.main()


class TemporaryPathTests(unittest.TestCase):
    """nginx must never fall back to a compiled-in path it cannot write.

    It creates a temporary directory for every proxying module compiled in, at
    startup and during `nginx -t`, whether or not the module is configured.
    Any left at its built-in default lands under the distribution's prefix —
    `/var/lib/nginx` on Arch — which the unit cannot write, because it runs as
    the desktop user under ProtectSystem=strict. Setup failed on exactly that.
    """

    # Every directive whose compiled-in default names a path nginx opens or
    # creates. `nginx -V` on Arch reports client-body, proxy, fastcgi, scgi and
    # uwsgi temp paths, an http log path, a pid path and a lock path; all of
    # them land outside Paddock state unless declared. Setup failed twice on
    # this — once on uwsgi, once on the access log — so the guard covers the
    # class rather than the two instances that happened to bite.
    DIRECTIVES = (
        "client_body_temp_path",
        "proxy_temp_path",
        "fastcgi_temp_path",
        "uwsgi_temp_path",
        "scgi_temp_path",
    )

    def test_every_temporary_path_is_relocated_into_paddock_state(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        paths = Paths.from_environment({
            "HOME": str(base / "home"),
            "XDG_CONFIG_HOME": str(base / "config"),
            "XDG_DATA_HOME": str(base / "data"),
            "XDG_STATE_HOME": str(base / "state"),
            "XDG_CACHE_HOME": str(base / "cache"),
        })
        main = WebProjector(paths).render({}).files["nginx.conf"]
        declared = {
            line.strip().split(None, 1)[0]
            for line in main.splitlines()
            if line.strip().split(None, 1)[:1]
            and line.strip().split(None, 1)[0].endswith("_temp_path")
        }
        self.assertEqual(set(self.DIRECTIVES), declared)
        for line in main.splitlines():
            if "_temp_path" in line:
                self.assertIn(str(paths.state), line, line)

    def test_no_writable_default_is_left_at_its_compiled_in_path(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        paths = Paths.from_environment({
            "HOME": str(base / "home"),
            "XDG_CONFIG_HOME": str(base / "config"),
            "XDG_DATA_HOME": str(base / "data"),
            "XDG_STATE_HOME": str(base / "state"),
            "XDG_CACHE_HOME": str(base / "cache"),
        })
        main = WebProjector(paths).render({}).files["nginx.conf"]
        for directive in ("pid", "error_log", "lock_file"):
            declared = [
                line.strip() for line in main.splitlines()
                if line.strip().startswith(directive + " ")
            ]
            self.assertEqual(1, len(declared), f"{directive}: {declared}")
            self.assertIn(str(paths.state), declared[0])
        # The http-level access log has no useful destination — each server
        # writes its own — so it is turned off rather than relocated.
        self.assertIn("access_log off;", main)
