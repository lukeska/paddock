from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from paddock import siteconfig
from paddock.paths import Paths
from paddock.projectfile import ProjectFileError, parse
from paddock.runtimes import RuntimeRegistry
from paddock.sites import SiteError, SiteManager
from paddock.state import StateStore
from paddock.web import WebProjector
from support import site_configuration


class FakeNginx:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, command, **_kwargs):
        self.calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")


class SiteConfigurationTests(unittest.TestCase):
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
        self.store = StateStore(self.paths)
        self.store.initialize()
        php = base / "php"
        php.write_text("php", encoding="utf-8")
        php.chmod(0o755)
        RuntimeRegistry(self.store).register("8.4", php)
        self.store.write("settings", {"schema_version": 1, "default_php": "8.4"})
        self.projector = WebProjector(self.paths, FakeNginx())
        self.manager = SiteManager(self.store, self.projector)
        self.app = base / "app"
        (self.app / "public").mkdir(parents=True)
        (self.app / "artisan").write_text("php", encoding="utf-8")
        self.fragment = self.app / ".paddock/nginx.conf"
        self.fragment.parent.mkdir(parents=True)
        self.fragment.write_text("client_max_body_size 512m;\n", encoding="utf-8")
        self.manager.link(self.app, "app", reload=False)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def status(self) -> str:
        return self.manager.configuration("app").status

    def rendered(self) -> str:
        return site_configuration(self.projector, "app")

    def test_nothing_is_declared_by_default(self) -> None:
        configuration = self.manager.configuration("app")
        self.assertEqual(siteconfig.NONE, configuration.status)
        self.assertFalse(configuration.user_present)
        self.assertEqual((), configuration.includable)

    def test_a_declaration_alone_serves_nothing(self) -> None:
        # The whole point: arbitrary server configuration arriving with a
        # clone is inert until someone has looked at it.
        self.manager.declare_project_configuration("app", ".paddock/nginx.conf", reload=False)
        self.assertEqual(siteconfig.PENDING, self.status())
        self.assertNotIn("nginx.conf", self.rendered())

    def test_trust_applies_it_and_records_the_digest(self) -> None:
        self.manager.declare_project_configuration("app", ".paddock/nginx.conf", reload=False)
        self.manager.trust_project_configuration("app", trusted=True, reload=False)
        self.assertEqual(siteconfig.TRUSTED, self.status())
        self.assertIn(str(self.fragment), self.rendered())
        recorded = self.store.read("sites")["sites"]["app"]["nginx"]["sha256"]
        self.assertEqual(siteconfig.digest(self.fragment), recorded)

    def test_a_changed_fragment_withdraws_its_own_trust(self) -> None:
        self.manager.declare_project_configuration("app", ".paddock/nginx.conf", reload=False)
        self.manager.trust_project_configuration("app", trusted=True, reload=False)
        # A pull, or an edit. Nobody has to notice: trust was granted to
        # contents, and the contents are no longer those.
        self.fragment.write_text("client_max_body_size 1g;\n", encoding="utf-8")
        self.assertEqual(siteconfig.CHANGED, self.status())
        self.manager.reproject(reload=False)
        self.assertNotIn(str(self.fragment), self.rendered())

    def test_a_deleted_fragment_reports_missing_rather_than_failing(self) -> None:
        self.manager.declare_project_configuration("app", ".paddock/nginx.conf", reload=False)
        self.fragment.unlink()
        self.assertEqual(siteconfig.MISSING, self.status())
        self.manager.reproject(reload=False)
        self.assertNotIn("nginx.conf", self.rendered())

    def test_revoking_keeps_the_declaration_and_drops_the_trust(self) -> None:
        self.manager.declare_project_configuration("app", ".paddock/nginx.conf", reload=False)
        self.manager.trust_project_configuration("app", trusted=True, reload=False)
        configuration = self.manager.trust_project_configuration(
            "app", trusted=False, reload=False
        )
        self.assertEqual(siteconfig.PENDING, configuration.status)
        self.assertEqual(
            ".paddock/nginx.conf",
            self.store.read("sites")["sites"]["app"]["nginx"]["path"],
        )

    def test_trusting_nothing_is_an_error_rather_than_a_silent_success(self) -> None:
        with self.assertRaisesRegex(SiteError, "declares no project nginx"):
            self.manager.trust_project_configuration("app", trusted=True, reload=False)

    def test_the_user_fragment_needs_no_trust_and_wins(self) -> None:
        user = siteconfig.user_path(self.paths, "app")
        user.parent.mkdir(parents=True, exist_ok=True)
        user.write_text("client_max_body_size 2g;\n", encoding="utf-8")
        self.manager.declare_project_configuration("app", ".paddock/nginx.conf", reload=False)
        self.manager.trust_project_configuration("app", trusted=True, reload=False)
        rendered = self.rendered()
        # The person at the keyboard outranks the repository, and nginx takes
        # the last of two conflicting directives.
        self.assertLess(rendered.index(str(self.fragment)), rendered.index(str(user)))

    def test_promoted_fragment_survives_a_broken_source_edit(self) -> None:
        user = siteconfig.user_path(self.paths, "app")
        user.parent.mkdir(parents=True, exist_ok=True)
        user.write_text("client_max_body_size 2g;\n", encoding="utf-8")
        self.manager.reproject(reload=False)
        promoted = self.projector.current / "fragments/app-00.conf"
        self.assertIn("client_max_body_size 2g", promoted.read_text(encoding="utf-8"))

        user.write_text("this_is_not_a_real_nginx_directive on;\n", encoding="utf-8")
        frozen = promoted.read_text(encoding="utf-8")
        self.assertIn("client_max_body_size 2g", frozen)
        self.assertNotIn("this_is_not_a_real", frozen)
        self.assertIn('include "fragments/app-00.conf";', self.rendered())
        self.assertNotIn(f"include {user};", self.rendered())

    def test_a_user_fragment_is_picked_up_by_reprojecting(self) -> None:
        user = siteconfig.user_path(self.paths, "app")
        self.assertNotIn(str(user), self.rendered())
        user.parent.mkdir(parents=True, exist_ok=True)
        user.write_text("# nothing\n", encoding="utf-8")
        self.manager.reproject(reload=False)
        self.assertIn(str(user), self.rendered())

    def test_relinking_preserves_a_declaration(self) -> None:
        self.manager.declare_project_configuration("app", ".paddock/nginx.conf", reload=False)
        self.manager.trust_project_configuration("app", trusted=True, reload=False)
        self.manager.link(self.app, "app", reload=False)
        self.assertEqual(siteconfig.TRUSTED, self.status())

    def test_pointing_at_a_different_file_drops_the_trust(self) -> None:
        self.manager.declare_project_configuration("app", ".paddock/nginx.conf", reload=False)
        self.manager.trust_project_configuration("app", trusted=True, reload=False)
        other = self.app / "other.conf"
        other.write_text("# other\n", encoding="utf-8")
        self.manager.declare_project_configuration("app", "other.conf", reload=False)
        # Trust belonged to contents somebody read, not to a key in a YAML
        # document, so a new target starts unreviewed.
        self.assertEqual(siteconfig.PENDING, self.status())


class DeclarationTests(unittest.TestCase):
    def test_the_project_file_accepts_and_confines_a_fragment(self) -> None:
        self.assertEqual(".paddock/nginx.conf", parse({"nginx": ".paddock/nginx.conf"}).nginx)
        self.assertIsNone(parse({}).nginx)
        for escape in ("/etc/nginx/nginx.conf", "../elsewhere.conf"):
            with self.subTest(escape=escape), self.assertRaises(ProjectFileError):
                parse({"nginx": escape})

    def test_declare_preserves_trust_only_for_the_same_path(self) -> None:
        record = {"name": "app", "nginx": {"path": "a.conf", "sha256": "0" * 64}}
        self.assertEqual("0" * 64, siteconfig.declare(record, "a.conf")["nginx"]["sha256"])
        self.assertNotIn("sha256", siteconfig.declare(record, "b.conf")["nginx"])
        self.assertNotIn("nginx", siteconfig.declare(record, None))


if __name__ == "__main__":
    unittest.main()


class ConfigCommandTests(unittest.TestCase):
    """`paddock config` takes two optional positionals, which is ambiguous."""

    def test_a_lone_argument_is_read_as_a_site(self) -> None:
        from paddock.cli import config_target

        # Showing is the default action, so this is the form people type.
        self.assertEqual(("show", "my-app"), config_target("my-app", None))
        self.assertEqual(("show", None), config_target("show", None))
        self.assertEqual(("trust", None), config_target("trust", None))
        self.assertEqual(("trust", "my-app"), config_target("trust", "my-app"))
        # A site named after an action stays reachable.
        self.assertEqual(("show", "trust"), config_target("show", "trust"))

    def test_a_mistyped_action_names_the_real_ones(self) -> None:
        from paddock.cli import config_target

        with self.assertRaisesRegex(SiteError, "revoke, show, trust"):
            config_target("trustt", "my-app")

    def test_the_parser_accepts_every_documented_form(self) -> None:
        # The bug this guards: `action` once carried argparse `choices`, so a
        # site name in that position was rejected before anything could read it.
        from paddock.cli import build

        parser, _ = build()
        for argv in (["config"], ["config", "my-app"], ["config", "show", "my-app"],
                     ["config", "trust"], ["config", "edit", "my-app"]):
            with self.subTest(argv=argv):
                parser.parse_args(argv)
