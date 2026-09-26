from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from release import catalog as promotion


ROOT = Path(__file__).parents[1]


class CatalogPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _prepared(self, kind: str, revision: int) -> bytes:
        packaged = ROOT / "resources" / ("artifacts.json" if kind == "php" else "node-artifacts.json")
        source = json.loads(packaged.read_text(encoding="utf-8"))
        return (json.dumps({**source, "revision": revision}, sort_keys=True) + "\n").encode()

    def test_prepare_increments_revision_and_emits_deterministic_bytes(self) -> None:
        candidate = ROOT / "resources/node-artifacts.json"
        current = self.base / "current.json"
        current.write_bytes(self._prepared("node", 7))
        output = self.base / "node.json"
        with patch.object(promotion, "validate_artifacts", return_value=["verified"]) as validate:
            evidence = promotion.prepare("node", candidate, current, output)
        self.assertEqual(["verified"], evidence)
        self.assertEqual(8, json.loads(output.read_text())["revision"])
        self.assertTrue(output.read_bytes().endswith(b"\n"))
        validate.assert_called_once()
        with self.assertRaisesRegex(promotion.PromotionError, "replace output"):
            promotion.prepare("node", candidate, current, output)

    def test_coverage_rejects_missing_architecture_and_patch_mismatch(self) -> None:
        source = json.loads(self._prepared("node", 1))
        source["artifacts"].pop()
        catalog = promotion._catalog(json.dumps(source).encode(), "node")
        with self.assertRaisesRegex(promotion.PromotionError, "coverage mismatch"):
            promotion._coverage(catalog)
        source = json.loads(self._prepared("node", 1))
        source["artifacts"][0]["node"] = "22.24.0"
        source["artifacts"][0]["url"] = source["artifacts"][0]["url"].replace("22.23.2", "22.24.0")
        catalog = promotion._catalog(json.dumps(source).encode(), "node")
        with self.assertRaisesRegex(promotion.PromotionError, "same patch"):
            promotion._coverage(catalog)

    def test_prepare_rejects_bad_artifact_checksum_before_writing(self) -> None:
        candidate = ROOT / "resources/node-artifacts.json"
        output = self.base / "node.json"
        def wrong_hash(_url, destination, **_kwargs):
            destination.write_bytes(b"wrong")
            return hashlib.sha256(b"wrong").hexdigest()
        with patch.object(promotion, "_download", side_effect=wrong_hash):
            with self.assertRaisesRegex(promotion.PromotionError, "checksum mismatch"):
                promotion.prepare("node", candidate, None, output)
        self.assertFalse(output.exists())

    def test_prepare_rejects_regressed_or_mutated_existing_patch(self) -> None:
        candidate = ROOT / "resources/node-artifacts.json"
        current = self.base / "current.json"
        output = self.base / "node.json"
        document = json.loads(self._prepared("node", 7))
        for item in document["artifacts"]:
            if item["major"] == "24":
                item["node"] = "24.21.0"
                item["url"] = item["url"].replace("24.20.0", "24.21.0")
        current.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(promotion.PromotionError, "regressed"):
            promotion.prepare("node", candidate, current, output)
        document = json.loads(self._prepared("node", 7))
        document["artifacts"][0]["sha256"] = "0" * 64
        current.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(promotion.PromotionError, "changed an existing patch"):
            promotion.prepare("node", candidate, current, output)

    def test_php_attestation_requires_matching_workflow_and_digest(self) -> None:
        archive = self.base / "paddock-php-8.4.25-linux-x86_64.tar.gz"
        archive.write_bytes(b"archive")
        digest = hashlib.sha256(b"archive").hexdigest()
        statement = [{"verificationResult": {
            "signature": {"certificate": {
                "sourceRepositoryDigest": "a" * 40,
                "runnerEnvironment": "github-hosted",
                "buildSignerURI": "https://github.com/lukeska/paddock/.github/workflows/runtime-release.yml@refs/heads/main",
            }},
            "statement": {"subject": [{"name": archive.name, "digest": {"sha256": digest}}]},
        }}]
        result = type("Result", (), {"returncode": 0, "stdout": json.dumps(statement), "stderr": ""})()
        with patch.object(promotion.subprocess, "run", return_value=result):
            self.assertEqual("a" * 40, promotion._php_provenance(archive, digest))
            with self.assertRaisesRegex(promotion.PromotionError, "does not match"):
                promotion._php_provenance(archive, "0" * 64)

    def test_node_checksum_list_must_pin_the_exact_asset(self) -> None:
        catalog = promotion._catalog(self._prepared("node", 1), "node")
        artifact = catalog.manifest.artifacts[0]
        sums = {artifact.node: f"{artifact.sha256}  {artifact.url.rsplit('/', 1)[-1]}\n"}
        promotion._node_provenance(artifact, artifact.sha256, sums)
        with self.assertRaisesRegex(promotion.PromotionError, "does not pin"):
            promotion._node_provenance(artifact, "0" * 64, sums)

    def test_publication_refuses_existing_or_skipped_archive(self) -> None:
        pages = self.base / "pages"
        pages.mkdir()
        first = self._prepared("php", 1)
        paths = promotion._publish_files(pages, "php", first, b"sig", 1)
        self.assertEqual(first, paths[0].read_bytes())
        with self.assertRaisesRegex(promotion.PromotionError, "already has"):
            promotion._publish_files(pages, "php", first, b"sig", 1)
        paths[2].parent.mkdir(parents=True, exist_ok=True)
        paths[2].write_bytes(first)
        paths[3].write_bytes(b"sig")
        with self.assertRaisesRegex(promotion.PromotionError, "revision must be 2"):
            promotion._publish_files(pages, "php", self._prepared("php", 3), b"sig", 3)

    def test_publication_refuses_dangling_archive_symlink(self) -> None:
        pages = self.base / "pages"
        archive = pages / "runtime-catalogs/archive/php/1.json"
        archive.parent.mkdir(parents=True)
        archive.symlink_to("missing")
        with self.assertRaisesRegex(promotion.PromotionError, "already has"):
            promotion._publish_files(pages, "php", self._prepared("php", 1), b"sig", 1)

    def test_publication_refuses_symlinked_directory(self) -> None:
        pages = self.base / "pages"
        outside = self.base / "outside"
        pages.mkdir()
        outside.mkdir()
        (pages / "runtime-catalogs").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(promotion.PromotionError, "directory is a symlink"):
            promotion._publish_files(pages, "php", self._prepared("php", 1), b"sig", 1)
        self.assertEqual([], list(outside.iterdir()))

    def test_verify_uses_pinned_public_key(self) -> None:
        catalog = self.base / "php.json"
        signature = self.base / "php.json.sig"
        catalog.write_bytes(self._prepared("php", 1))
        signature.write_bytes(b"signature")
        with patch.object(promotion.CatalogStore, "_verify") as verify:
            signed = promotion.verify("php", catalog, signature)
        self.assertEqual(1, signed.revision)
        verify.assert_called_once_with(catalog.read_bytes(), b"signature")


if __name__ == "__main__":
    unittest.main()
