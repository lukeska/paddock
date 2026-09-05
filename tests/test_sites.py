from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from paddock.web import WebError, WebProjector
from paddock.paths import Paths
from paddock.runtimes import RuntimeRegistry
from paddock.sites import SiteError, SiteManager, normalize_site_name
from paddock.state import StateStore
from support import promoted, site_configuration


class FakeNginx:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.reject_validation = False
        self.reject_reload = False

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        rejected = ("-t" in command and self.reject_validation) or (
            "reload" in command and self.reject_reload
        )
        return subprocess.CompletedProcess(
            command, 1 if rejected else 0, "", "rejected by fake nginx" if rejected else ""
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
        self.fake = FakeNginx()
        self.projector = WebProjector(paths, self.fake)
        self.manager = SiteManager(self.store, self.projector)
        # A Laravel project, because that is what these cases are about.
        # A bare public/ with no entry point now detects as a static site.
        self.app = base / "My App ü"
        (self.app / "public").mkdir(parents=True)
        (self.app / "artisan").write_text("#!/usr/bin/env php\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_link_renders_validated_deterministic_projection(self) -> None:
        site = self.manager.link(self.app, "demo", reload=False)
        self.assertEqual(site.name, "demo")
        rendered = site_configuration(self.projector, "demo")
        self.assertIn("server_name demo.test;", rendered)
        self.assertIn("listen 127.0.0.1:80;", rendered)
        self.assertIn('root "' + str(self.app / "public") + '";', rendered)
        self.assertIn("php/8.4/fpm.sock", rendered)
        self.assertEqual(self.fake.calls[0][1], "-t")
        self.assertEqual(self.store.read("sites")["sites"]["demo"]["root"], str(self.app))

    def test_invalid_candidate_preserves_registry_and_projection(self) -> None:
        self.manager.link(self.app, "good", reload=False)
        before_registry = self.store.path_for("sites").read_bytes()
        before_projection = promoted(self.projector)
        before_generation = self.projector.current.resolve()
        second = Path(self.temporary.name) / "second"
        (second / "public").mkdir(parents=True)
        self.fake.reject_validation = True
        with self.assertRaises(WebError):
            self.manager.link(second, "bad", reload=False)
        self.assertEqual(self.store.path_for("sites").read_bytes(), before_registry)
        self.assertEqual(promoted(self.projector), before_projection)
        # The symlink still points at the generation that was serving, and the
        # rejected tree left nothing behind to be promoted by a later reload.
        self.assertEqual(before_generation, self.projector.current.resolve())
        self.assertFalse((self.projector.current / "sites/bad.conf").exists())

    def test_unlink_by_nested_current_directory(self) -> None:
        self.manager.link(self.app, "demo", reload=False)
        nested = self.app / "src" / "nested"
        nested.mkdir(parents=True)
        removed = self.manager.unlink(directory=nested, reload=False)
        self.assertEqual(removed.name, "demo")
        self.assertEqual(self.store.read("sites")["sites"], {})
        self.assertNotIn("demo.test", promoted(self.projector))

    def test_duplicate_root_is_rejected(self) -> None:
        self.manager.link(self.app, "first", reload=False)
        with self.assertRaises(SiteError):
            self.manager.link(self.app, "second", reload=False)

    def test_a_project_without_public_links_as_static(self) -> None:
        # This used to be refused outright. A directory with no recognizable
        # layout is a static site served from itself, which is the whole point
        # of dropping the Laravel-only assumption.
        plain = Path(self.temporary.name) / "brochure"
        plain.mkdir()
        (plain / "index.html").write_text("hi", encoding="utf-8")
        site = self.manager.link(plain, "brochure", reload=False)
        self.assertEqual("static", site.driver)
        self.assertEqual(".", site.document_root)
        self.assertEqual(plain, site.served)
        rendered = site_configuration(self.projector, "brochure")
        self.assertIn('root "' + str(plain) + '";', rendered)
        # A static site must never hand a .php file to FPM.
        self.assertNotIn("fastcgi_pass", rendered)
        self.assertIn("try_files $uri $uri/ =404;", rendered)

    def test_a_missing_document_root_is_still_rejected(self) -> None:
        # Detection finding Laravel and Laravel's public/ being absent is a
        # broken project, not a static site.
        broken = Path(self.temporary.name) / "half-cloned"
        broken.mkdir()
        (broken / "artisan").write_text("#!/usr/bin/env php\n", encoding="utf-8")
        with self.assertRaisesRegex(SiteError, "no document root"):
            self.manager.link(broken, "broken", reload=False)

    def test_detected_type_is_recorded_and_preserved_across_relinking(self) -> None:
        wordpress = Path(self.temporary.name) / "site"
        wordpress.mkdir()
        (wordpress / "wp-config.php").write_text("<?php\n", encoding="utf-8")
        site = self.manager.link(wordpress, "wp", reload=False)
        self.assertEqual("wordpress", site.driver)
        self.assertEqual(".", site.document_root)
        rendered = site_configuration(self.projector, "wp")
        self.assertIn("try_files $uri $uri/ /index.php?$args;", rendered)
        # The upload denial has to precede the shared PHP location, because
        # nginx takes the first matching regex.
        self.assertLess(
            rendered.index("wp-content/uploads"), rendered.index("location ~ \\.php$")
        )
        # A later link with no explicit type keeps the recorded one, so
        # `paddock init` cannot silently revert a deliberate choice.
        again = self.manager.link(wordpress, "wp", reload=False)
        self.assertEqual("wordpress", again.driver)

    def test_an_explicit_type_overrides_detection_and_recomputes_the_root(self) -> None:
        project = Path(self.temporary.name) / "legacy"
        (project / "public").mkdir(parents=True)
        (project / "wp-config.php").write_text("<?php\n", encoding="utf-8")
        detected = self.manager.link(project, "legacy", reload=False)
        self.assertEqual("wordpress", detected.driver)
        chosen = self.manager.link(project, "legacy", driver="laravel", reload=False)
        self.assertEqual("laravel", chosen.driver)
        self.assertEqual("public", chosen.document_root)
        with self.assertRaises(ValueError):
            self.manager.link(project, "legacy", driver="drupal", reload=False)

    def test_a_document_root_cannot_escape_the_project(self) -> None:
        # It arrives from a committed project file, so a clone must not be
        # able to point a .test host at somebody's home directory.
        for escape in ("../..", "/etc", "public/../../.."):
            with self.subTest(escape=escape), self.assertRaises(ValueError):
                self.manager.link(self.app, "demo", document_root=escape, reload=False)

    def test_names_are_normalized_without_allowing_nginx_injection(self) -> None:
        self.assertEqual(normalize_site_name("Demo.test"), "demo")
        for invalid in ("-demo", "demo_thing", "demo.test.test", "demo { evil"):
            with self.subTest(invalid=invalid), self.assertRaises(SiteError):
                normalize_site_name(invalid)

    def test_reload_failure_keeps_valid_files_for_retry(self) -> None:
        self.fake.reject_reload = True
        with self.assertRaises(WebError):
            self.manager.link(self.app, "demo")
        self.assertIn("demo", self.store.read("sites")["sites"])
        self.assertIn("demo.test", promoted(self.projector))

