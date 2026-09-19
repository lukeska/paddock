from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import tarfile
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).parents[1]
PKGBUILD = ROOT / "packaging/arch/PKGBUILD"
WORKFLOW = ROOT / ".github/workflows/application-release.yml"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


class ApplicationReleaseTests(unittest.TestCase):
    @unittest.skipUnless(PKGBUILD.is_file(), "requires a repository checkout")
    def test_release_metadata_agrees_on_version_and_remote_source(self) -> None:
        result = subprocess.run(
            ["python", "release/validate-application-release.py", f"v{VERSION}"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(VERSION, result.stdout.strip())

    @unittest.skipUnless(PKGBUILD.is_file(), "requires a repository checkout")
    def test_a_wrong_tag_is_rejected(self) -> None:
        result = subprocess.run(
            ["python", "release/validate-application-release.py", "v999.0.0"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("does not match", result.stderr)

    @unittest.skipUnless((ROOT / ".git").exists(), "requires Git release inputs")
    def test_source_archive_is_reproducible_and_excludes_its_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.tar.gz"
            second = Path(temporary) / "second.tar.gz"
            for output in (first, second):
                subprocess.run(
                    ["bash", "release/build-source.sh", str(output), VERSION],
                    cwd=ROOT, check=True, stdout=subprocess.DEVNULL,
                )
            self.assertEqual(
                hashlib.sha256(first.read_bytes()).digest(),
                hashlib.sha256(second.read_bytes()).digest(),
            )
            package = PKGBUILD.read_text(encoding="utf-8")
            expected = package.split("sha256sums=('", 1)[1].split("'", 1)[0]
            self.assertEqual(expected, hashlib.sha256(first.read_bytes()).hexdigest())
            with tarfile.open(first, "r:gz") as archive:
                names = archive.getnames()
            self.assertTrue(names)
            prefix = f"paddock-{VERSION}/"
            self.assertTrue(all(name.startswith(prefix) for name in names))
            self.assertNotIn(f"{prefix}packaging/arch/PKGBUILD", names)

    def test_tag_release_waits_for_tests_package_and_clean_install(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('tags:\n      - "v*"', workflow)
        self.assertIn("./release/validate-application-release.py", workflow)
        self.assertIn("./tests/package/clean-install.sh", workflow)
        self.assertIn("needs: [source, package, clean-install]", workflow)
        self.assertIn("--verify-tag", workflow)
        self.assertIn("contents: write", workflow)

    @unittest.skipUnless(PKGBUILD.is_file(), "requires a repository checkout")
    def test_local_build_uses_a_temporary_revision_25_recipe(self) -> None:
        script = (ROOT / "packaging/arch/build-local.sh").read_text(encoding="utf-8")
        self.assertIn("PKGBUILD.local", script)
        self.assertIn("pkgrel=25", script)
        self.assertNotIn("sed -i", script)
        package = PKGBUILD.read_text(encoding="utf-8")
        self.assertIn("pkgrel=1", package)
        self.assertIn("/releases/download/v$pkgver/", package)


if __name__ == "__main__":
    unittest.main()
