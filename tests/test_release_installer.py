from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).parents[1]
INSTALLER = ROOT / "scripts/install-release.sh"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
PACKAGE = f"paddock-{VERSION}-1-x86_64.pkg.tar.zst"


class ReleaseInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.release = self.root / "release"
        self.release.mkdir()
        self.calls = self.root / "calls"
        package = self.release / PACKAGE
        package.write_bytes(b"test Arch package")
        self.digest = hashlib.sha256(package.read_bytes()).hexdigest()
        self.write_manifest(self.digest)
        self.executable(
            "curl",
            """#!/usr/bin/env bash
set -euo pipefail
while (( $# )); do
  case "$1" in
    --output) output=$2; shift 2 ;;
    *) url=$1; shift ;;
  esac
done
cp "$TEST_RELEASE_DIR/${url##*/}" "$output"
""",
        )
        self.executable(
            "sudo",
            """#!/usr/bin/env bash
printf 'sudo %s\\n' "$*" >> "$TEST_CALLS"
""",
        )
        self.executable("pacman", "#!/usr/bin/env bash\nexit 0\n")
        self.executable(
            "paddock",
            """#!/usr/bin/env bash
printf 'paddock %s\\n' "$*" >> "$TEST_CALLS"
""",
        )
        self.executable(
            "uname",
            """#!/usr/bin/env bash
printf '%s\\n' "$TEST_ARCH"
""",
        )

    def executable(self, name: str, content: str) -> None:
        path = self.bin / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def write_manifest(self, digest: str) -> None:
        (self.release / "SHA256SUMS").write_text(
            f"{digest}  {PACKAGE}\n", encoding="utf-8"
        )

    def run_installer(self, architecture: str = "x86_64") -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            PATH=f"{self.bin}:{environment['PATH']}",
            TEST_RELEASE_DIR=str(self.release),
            TEST_CALLS=str(self.calls),
            TEST_ARCH=architecture,
        )
        return subprocess.run(
            ["bash", str(INSTALLER)],
            cwd=self.root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_verified_package_is_installed_then_setup_and_doctor_run(self) -> None:
        result = self.run_installer()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"{PACKAGE}: OK", result.stdout)
        calls = self.calls.read_text(encoding="utf-8").splitlines()
        self.assertEqual(3, len(calls))
        self.assertTrue(calls[0].startswith("sudo pacman -U --needed -- /"))
        self.assertTrue(calls[0].endswith(f"/{PACKAGE}"))
        self.assertEqual(["paddock setup --yes", "paddock doctor"], calls[1:])

    def test_bad_checksum_stops_before_pacman(self) -> None:
        self.write_manifest("0" * 64)
        result = self.run_installer()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("FAILED", result.stdout)
        self.assertFalse(self.calls.exists())

    def test_unsupported_architecture_stops_before_download(self) -> None:
        result = self.run_installer("aarch64")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("No published Paddock package", result.stderr)
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
