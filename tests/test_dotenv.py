from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from paddock.dotenv import format_value, reconcile, render


class DotenvRenderingTests(unittest.TestCase):
    def test_updates_declared_keys_and_preserves_everything_else(self) -> None:
        original = "# local settings\nAPP_ENV=production\nKEEP=mine\n\n"
        self.assertEqual(
            "# local settings\nAPP_ENV=local\nKEEP=mine\n\nFEATURE_FLAG=true\n",
            render(original, {"APP_ENV": "local", "FEATURE_FLAG": "true"}),
        )

    def test_comments_do_not_count_as_active_assignments(self) -> None:
        self.assertEqual(
            "# APP_ENV=production\n\nAPP_ENV=local\n",
            render("# APP_ENV=production\n", {"APP_ENV": "local"}),
        )

    def test_duplicate_active_assignments_are_made_consistent(self) -> None:
        self.assertEqual(
            "APP_ENV=local\nAPP_ENV=local\n",
            render("APP_ENV=old\nAPP_ENV=new\n", {"APP_ENV": "local"}),
        )

    def test_empty_declaration_does_not_reformat_the_file(self) -> None:
        original = "KEY=value\n\n"
        self.assertEqual(original, render(original, {}))

    def test_values_are_encoded_as_literals_for_dotenv(self) -> None:
        self.assertEqual("local", format_value("local"))
        self.assertEqual('"My App"', format_value("My App"))
        self.assertEqual('"cost \\$5 \\"today\\""', format_value('cost $5 "today"'))


class DotenvReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_missing_env_starts_from_example_and_is_private(self) -> None:
        (self.root / ".env.example").write_text(
            "APP_NAME=Example\nAPP_ENV=production\n", encoding="utf-8"
        )
        self.assertTrue(reconcile(self.root, {"APP_ENV": "local"}))
        path = self.root / ".env"
        self.assertEqual(
            "APP_NAME=Example\nAPP_ENV=local\n", path.read_text(encoding="utf-8")
        )
        self.assertEqual(0o600, path.stat().st_mode & 0o777)

    def test_existing_mode_is_preserved_and_second_run_is_unchanged(self) -> None:
        path = self.root / ".env"
        path.write_text("APP_ENV=production\n", encoding="utf-8")
        path.chmod(0o640)
        self.assertTrue(reconcile(self.root, {"APP_ENV": "local"}))
        self.assertFalse(reconcile(self.root, {"APP_ENV": "local"}))
        self.assertEqual(0o640, path.stat().st_mode & 0o777)

    def test_dry_run_never_creates_or_changes_env(self) -> None:
        self.assertTrue(reconcile(self.root, {"APP_ENV": "local"}, dry_run=True))
        self.assertFalse((self.root / ".env").exists())

    def test_empty_declaration_does_not_create_env(self) -> None:
        self.assertFalse(reconcile(self.root, {}))
        self.assertFalse((self.root / ".env").exists())

    def test_symbolic_links_are_refused(self) -> None:
        target = self.root / "target.env"
        target.write_text("SECRET=keep\n", encoding="utf-8")
        (self.root / ".env").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            reconcile(self.root, {"APP_ENV": "local"})
        self.assertEqual("SECRET=keep\n", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
