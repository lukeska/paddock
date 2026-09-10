from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PhpReleaseMatrixTests(unittest.TestCase):
    def test_every_php_8_minor_has_one_pinned_release(self) -> None:
        matrix = json.loads((ROOT / "release/php/versions.json").read_text())
        runtimes = matrix["runtimes"]

        self.assertEqual(
            ["8.0", "8.1", "8.2", "8.3", "8.4", "8.5"],
            [runtime["minor"] for runtime in runtimes],
        )
        self.assertEqual(len(runtimes), len({runtime["minor"] for runtime in runtimes}))
        for runtime in runtimes:
            self.assertTrue(runtime["php"].startswith(runtime["minor"] + "."))
            self.assertEqual(["x86_64"], runtime["architectures"])
            self.assertIn(runtime["support"], {"active", "security", "eol"})

    def test_release_workflow_can_build_each_minor_individually(self) -> None:
        workflow = (ROOT / ".github/workflows/runtime-release.yml").read_text()
        for minor in ("8.0", "8.1", "8.2", "8.3", "8.4", "8.5"):
            self.assertIn(f'- "{minor}"', workflow)

    def test_php_80_pins_a_compatible_libxml2_source(self) -> None:
        build = (ROOT / "experiments/phase-0/php/build-runtime.sh").read_text()
        self.assertIn('[[ "$php_version" == 8.0.* || "$php_version" == 8.1.* ]]', build)
        self.assertIn("libxml2-2.12.10.tar.xz", build)
        self.assertIn("libxslt-1.1.42.tar.xz", build)
        self.assertIn("icu4c-72_1-src.tgz", build)
        self.assertIn('SPC_DEFAULT_C_FLAGS="-fPIC -Os -std=gnu17"', build)

        release_build = (ROOT / "release/php/build.sh").read_text()
        self.assertIn('[[ "$php_version" == 8.0.* || "$php_version" == 8.1.* ]]', release_build)
        self.assertIn('SPC_DEFAULT_C_FLAGS="-fPIC -Os -std=gnu17"', release_build)
        self.assertIn("PADDOCK_RELEASE_DOWNLOAD_CACHE", release_build)
        self.assertIn('cp -a -- "$download_cache/."', release_build)
        self.assertIn('rm -f -- "$workspace/downloads/.lock.json"', release_build)
        self.assertIn('cp -an -- "$workspace/downloads/."', release_build)

        optional_build = (
            ROOT / "experiments/phase-0/php/build-optional-extension.sh"
        ).read_text()
        self.assertIn('"$php_minor" == 8.0 || "$php_minor" == 8.1', optional_build)
        self.assertIn("xdebug/archive/refs/tags/3.2.2.tar.gz", optional_build)


if __name__ == "__main__":
    unittest.main()
