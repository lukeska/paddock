from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from paddock.caddy import CaddyError, CaddyProjector
from paddock.paths import Paths
from paddock.runtimes import RuntimeRegistry
from paddock.sites import SiteError, SiteManager, normalize_site_name
from paddock.state import StateStore


class FakeCaddy:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.reject_validation = False
        self.reject_reload = False

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        rejected = ("validate" in command and self.reject_validation) or (
            "reload" in command and self.reject_reload
        )
        return subprocess.CompletedProcess(
            command, 1 if rejected else 0, "", "rejected by fake caddy" if rejected else ""
        )


class SiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        paths = Paths.from_environment(
            {
                "HOME": str(base / "home"),
                "XDG_CONFIG_HOME": str(base / "config"),
                "XDG_DATA_HOME": str(base / "data"),
                "XDG_STATE_HOME": str(base / "state"),
                "XDG_CACHE_HOME": str(base / "cache"),
            },
            runtime_root=base / "run with space" / "paddock",
        )
        self.store = StateStore(paths)
        self.store.initialize()
        php = base / "PHP 8.4" / "php"
        php.parent.mkdir()
        php.write_text("php", encoding="utf-8")
        php.chmod(0o755)
        RuntimeRegistry(self.store).register("8.4", php)
        self.store.write("settings", {"schema_version": 1, "default_php": "8.4"})
        self.fake = FakeCaddy()
        self.projector = CaddyProjector(paths, self.fake)
        self.manager = SiteManager(self.store, self.projector)
        self.app = base / "My App ü"
        (self.app / "public").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_link_renders_validated_deterministic_projection(self) -> None:
        site = self.manager.link(self.app, "demo", reload=False)
        self.assertEqual(site.name, "demo")
        rendered = self.projector.path.read_text(encoding="utf-8")
        self.assertIn("http://demo.test", rendered)
        self.assertIn('"' + str(self.app / "public") + '"', rendered)
        self.assertIn("php/8.4/fpm.sock", rendered)
        self.assertEqual(self.fake.calls[0][1], "validate")
        self.assertEqual(self.store.read("sites")["sites"]["demo"]["root"], str(self.app))

    def test_invalid_candidate_preserves_registry_and_projection(self) -> None:
        self.manager.link(self.app, "good", reload=False)
        before_registry = self.store.path_for("sites").read_bytes()
        before_projection = self.projector.path.read_bytes()
        second = Path(self.temporary.name) / "second"
        (second / "public").mkdir(parents=True)
        self.fake.reject_validation = True
        with self.assertRaises(CaddyError):
            self.manager.link(second, "bad", reload=False)
        self.assertEqual(self.store.path_for("sites").read_bytes(), before_registry)
        self.assertEqual(self.projector.path.read_bytes(), before_projection)

    def test_unlink_by_nested_current_directory(self) -> None:
        self.manager.link(self.app, "demo", reload=False)
        nested = self.app / "src" / "nested"
        nested.mkdir(parents=True)
        removed = self.manager.unlink(directory=nested, reload=False)
        self.assertEqual(removed.name, "demo")
        self.assertEqual(self.store.read("sites")["sites"], {})
        self.assertNotIn("demo.test", self.projector.path.read_text(encoding="utf-8"))

    def test_duplicate_root_and_missing_public_are_rejected(self) -> None:
        self.manager.link(self.app, "first", reload=False)
        with self.assertRaises(SiteError):
            self.manager.link(self.app, "second", reload=False)
        missing = Path(self.temporary.name) / "missing-public"
        missing.mkdir()
        with self.assertRaises(SiteError):
            self.manager.link(missing, "missing", reload=False)

    def test_names_are_normalized_without_allowing_caddy_injection(self) -> None:
        self.assertEqual(normalize_site_name("Demo.test"), "demo")
        for invalid in ("-demo", "demo_thing", "demo.test.test", "demo { evil"):
            with self.subTest(invalid=invalid), self.assertRaises(SiteError):
                normalize_site_name(invalid)

    def test_reload_failure_keeps_valid_files_for_retry(self) -> None:
        self.fake.reject_reload = True
        with self.assertRaises(CaddyError):
            self.manager.link(self.app, "demo")
        self.assertIn("demo", self.store.read("sites")["sites"])
        self.assertIn("demo.test", self.projector.path.read_text(encoding="utf-8"))

