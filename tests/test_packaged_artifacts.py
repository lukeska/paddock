"""Guard the artifact index that actually ships inside the Arch package.

`resources/artifacts.json` is installed to /usr/share/paddock/artifacts.json
and is the root of runtime trust: `RuntimeInstaller` downloads whatever URL it
names and accepts the archive only if it matches the pinned `sha256`. Nothing
re-verifies this file at install time, so a bad entry committed here ships to
users and fails on their machines rather than in CI.

`tests/test_release_index.py` covers the generator in `release/php/index.py`.
This covers the committed artifact, which is a different thing: a local build
writes `file://` URLs into `artifacts.local.json`, and only convention keeps
that out of the packaged index. Published runtimes must be the attested CI
builds, so every URL here has to be a public HTTPS release URL.
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest

from paddock.artifacts import ArtifactManifest


PACKAGED_INDEX = Path(__file__).parents[1] / "resources/artifacts.json"


class PackagedArtifactIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        # Loading through the production parser rather than json.load keeps
        # this honest: field names, PHP/minor agreement, architecture, and the
        # sha256 shape are all validated by the same code the CLI runs.
        self.manifest = ArtifactManifest.load(PACKAGED_INDEX)

    def test_index_is_not_empty(self) -> None:
        self.assertTrue(self.manifest.artifacts, "packaged index lists no runtimes")

    def test_every_url_is_a_public_https_url(self) -> None:
        # The parser only rejects an empty URL, so `file://` from a local
        # build would otherwise pass every check and ship.
        for artifact in self.manifest.artifacts:
            self.assertTrue(
                artifact.url.startswith("https://"),
                f"PHP {artifact.php} {artifact.architecture} must use an HTTPS "
                f"release URL, not {artifact.url!r}. A local build writes "
                f"file:// URLs; published runtimes must be the attested CI build.",
            )

    def test_no_duplicate_runtime_for_a_minor_and_architecture(self) -> None:
        # select() silently prefers the highest patch, so a stale duplicate
        # would be ignored rather than reported.
        seen: dict[tuple[str, str], str] = {}
        for artifact in self.manifest.artifacts:
            key = (artifact.minor, artifact.architecture)
            self.assertNotIn(
                key, seen,
                f"duplicate entry for PHP {key[0]} {key[1]}: "
                f"{seen.get(key)} and {artifact.php}",
            )
            seen[key] = artifact.php

    def test_each_url_names_the_archive_it_pins(self) -> None:
        # Catches a copy-paste that points one version's entry at another's
        # file while carrying the first version's hash.
        for artifact in self.manifest.artifacts:
            expected = f"paddock-php-{artifact.php}-linux-{artifact.architecture}.tar.gz"
            self.assertTrue(
                artifact.url.endswith(f"/{expected}"),
                f"URL for PHP {artifact.php} should end with {expected}: {artifact.url}",
            )

    def test_release_urls_are_pinned_to_an_immutable_tag(self) -> None:
        # A moving tag breaks checksum verification for already-installed
        # users, so the download path must name a specific release.
        for artifact in self.manifest.artifacts:
            self.assertRegex(
                artifact.url,
                r"^https://github\.com/[^/]+/[^/]+/releases/download/[^/]+/[^/]+$",
                f"expected an immutable release download URL: {artifact.url}",
            )

    def test_selection_works_for_every_listed_runtime(self) -> None:
        for artifact in self.manifest.artifacts:
            chosen = self.manifest.select(artifact.minor, artifact.architecture)
            self.assertEqual(artifact.architecture, chosen.architecture)
            self.assertEqual(artifact.minor, chosen.minor)


if __name__ == "__main__":
    unittest.main()
