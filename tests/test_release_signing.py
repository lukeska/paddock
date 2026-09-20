from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "release" / "sign-application-release.sh"
FINGERPRINT = "0123456789ABCDEF0123456789ABCDEF01234567"


class ReleaseSigningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.assets = base / "assets"
        self.bin = base / "bin"
        self.output = base / "output"
        self.assets.mkdir()
        self.bin.mkdir()

        files = {
            "PKGBUILD": b"pkgver=1.2.3\n",
            "paddock-1.2.3.tar.gz": b"source artifact\n",
            "paddock-1.2.3-1-x86_64.pkg.tar.zst": b"package artifact\n",
        }
        for name, contents in files.items():
            (self.assets / name).write_bytes(contents)
        sums = "".join(
            f"{hashlib.sha256(contents).hexdigest()}  {name}\n"
            for name, contents in files.items()
        )
        (self.assets / "SHA256SUMS").write_text(sums)

        self._write_executable(
            "gh",
            """
            #!/usr/bin/env bash
            set -euo pipefail
            if [[ $1 == release && $2 == view ]]; then
              printf '%s\n' ${FAKE_ASSET_NAMES}
            elif [[ $1 == release && $2 == download ]]; then
              destination=''
              while [[ $# -gt 0 ]]; do
                if [[ $1 == --dir ]]; then destination=$2; shift 2; else shift; fi
              done
              cp "$FAKE_ASSETS"/* "$destination"/
            elif [[ $1 == release && $2 == upload ]]; then
              printf 'uploaded\n' >> "$FAKE_GH_LOG"
            else
              exit 90
            fi
            """,
        )
        self._write_executable(
            "gpg",
            """
            #!/usr/bin/env bash
            set -euo pipefail
            if [[ " $* " == *" --list-secret-keys "* ]]; then
              exit 0
            elif [[ " $* " == *" --detach-sign "* ]]; then
              output=''
              previous=''
              for argument in "$@"; do
                if [[ $previous == --output ]]; then output=$argument; fi
                previous=$argument
              done
              printf 'signature\n' > "$output"
            elif [[ " $* " == *" --verify "* ]]; then
              printf '[GNUPG:] VALIDSIG %s 2026-09-20 0 4 0 22 8 00 %s\n' \
                "$FAKE_FINGERPRINT" "$FAKE_FINGERPRINT"
            else
              exit 91
            fi
            """,
        )

    def _write_executable(self, name: str, source: str) -> None:
        path = self.bin / name
        path.write_text(textwrap.dedent(source).lstrip())
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _environment(self, asset_names: list[str]) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{self.bin}:{environment['PATH']}",
                "FAKE_ASSETS": str(self.assets),
                "FAKE_ASSET_NAMES": " ".join(asset_names),
                "FAKE_FINGERPRINT": FINGERPRINT,
                "FAKE_GH_LOG": str(Path(self.temp.name) / "gh.log"),
                "PADDOCK_GITHUB_REPOSITORY": "example/paddock",
            }
        )
        return environment

    def test_signs_checksum_verified_release_artifacts(self) -> None:
        names = sorted(path.name for path in self.assets.iterdir())
        result = subprocess.run(
            [str(SCRIPT), "v1.2.3", FINGERPRINT, str(self.output)],
            cwd=ROOT,
            env=self._environment(names),
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.output / "paddock-1.2.3.tar.gz.sig").is_file())
        self.assertTrue(
            (self.output / "paddock-1.2.3-1-x86_64.pkg.tar.zst.sig").is_file()
        )
        self.assertIn("Signatures were not uploaded", result.stdout)

    def test_refuses_to_replace_a_published_signature(self) -> None:
        names = sorted(path.name for path in self.assets.iterdir())
        names.append("paddock-1.2.3.tar.gz.sig")
        result = subprocess.run(
            [str(SCRIPT), "v1.2.3", FINGERPRINT, str(self.output)],
            cwd=ROOT,
            env=self._environment(names),
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already contains signature assets", result.stderr)
        self.assertFalse(self.output.exists() and any(self.output.iterdir()))


if __name__ == "__main__":
    unittest.main()
