from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from paddock.catalog_store import CatalogStore, CatalogStoreError
from paddock.application import PaddockController
from paddock.paths import Paths
from paddock.runtime_catalog import CATALOG_URLS
from paddock.state import StateStore


FIXTURES = Path(__file__).parent / "fixtures/runtime_catalogs"


@unittest.skipUnless(shutil.which("gpg"), "requires GnuPG")
class CatalogStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.key_directory = tempfile.TemporaryDirectory()
        cls.key_home = Path(cls.key_directory.name) / "keyring"
        cls.key_home.mkdir(mode=0o700)
        subprocess.run([
            "gpg", "--homedir", str(cls.key_home), "--batch", "--pinentry-mode", "loopback",
            "--passphrase", "", "--quick-gen-key", "Paddock catalog test <test@example.invalid>",
            "ed25519", "sign", "0",
        ], check=True, capture_output=True, timeout=30)
        fingerprints = subprocess.run([
            "gpg", "--homedir", str(cls.key_home), "--batch", "--with-colons",
            "--fingerprint", "Paddock catalog test",
        ], check=True, text=True, capture_output=True, timeout=15).stdout
        cls.fingerprint = next(line.split(":")[9] for line in fingerprints.splitlines() if line.startswith("fpr:"))
        cls.public_key = Path(cls.key_directory.name) / "public.asc"
        cls.public_key.write_bytes(subprocess.run([
            "gpg", "--homedir", str(cls.key_home), "--batch", "--armor", "--export",
            cls.fingerprint,
        ], check=True, capture_output=True, timeout=15).stdout)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.key_directory.cleanup()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.paths = Paths.from_environment({
            "HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_DATA_HOME": str(root / "data"), "XDG_STATE_HOME": str(root / "state"),
            "XDG_CACHE_HOME": str(root / "cache"),
        }, runtime_root=root / "run")
        self.paths.initialize()
        self.responses: dict[str, bytes] = {}
        self.store = CatalogStore(self.paths, key_path=self.public_key,
                                  fingerprint=self.fingerprint, fetch=self.fetch)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def fetch(self, url: str, _limit: int) -> bytes:
        return self.responses[url]

    def publish(self, kind: str, fixture: str, *, mutate=None) -> bytes:
        document = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
        if mutate is not None:
            mutate(document)
        payload = (json.dumps(document, sort_keys=True) + "\n").encode()
        source = Path(self.temporary.name) / "signed.json"
        signature = Path(self.temporary.name) / "signed.json.sig"
        source.write_bytes(payload)
        subprocess.run([
            "gpg", "--homedir", str(self.key_home), "--batch", "--yes",
            "--local-user", self.fingerprint, "--output", str(signature),
            "--detach-sign", str(source),
        ], check=True, capture_output=True, timeout=15)
        self.responses[CATALOG_URLS[kind]] = payload
        self.responses[CATALOG_URLS[kind] + ".sig"] = signature.read_bytes()
        return payload

    def test_signed_refresh_is_cached_and_selected(self) -> None:
        payload = self.publish("php", "php-valid.json")
        accepted = self.store.refresh("php")
        self.assertEqual(("refreshed", 2), (accepted.source, accepted.revision))
        self.assertEqual("refreshed", self.store.effective("php").source)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), self.store._metadata("php")["sha256"])

    def test_controller_snapshots_use_refreshed_catalogs(self) -> None:
        self.publish("php", "php-valid.json")
        self.publish("node", "node-valid.json")
        self.store.refresh("php")
        self.store.refresh("node")
        state = StateStore(self.paths)
        state.initialize()
        with mock.patch("paddock.application.CatalogStore", return_value=self.store):
            controller = PaddockController(state)
            php = controller.php_versions_snapshot().versions
            node = controller.node_versions_snapshot().versions
        self.assertEqual(["8.4"], [item.minor for item in php])
        self.assertEqual(["24"], [item.major for item in node])

    def test_signature_failure_does_not_replace_last_trusted_catalog(self) -> None:
        self.publish("php", "php-valid.json")
        self.store.refresh("php")
        self.responses[CATALOG_URLS["php"]] += b" "
        with self.assertRaisesRegex(CatalogStoreError, "signature"):
            self.store.refresh("php")
        self.assertEqual(2, self.store.effective("php").revision)

    def test_replay_and_changed_equal_revision_are_rejected(self) -> None:
        self.publish("php", "php-valid.json")
        self.store.refresh("php")
        self.publish("php", "php-older.json")
        with self.assertRaisesRegex(CatalogStoreError, "older than accepted"):
            self.store.refresh("php")
        self.publish("php", "php-valid.json", mutate=lambda data: data["artifacts"][0].update(sha256="a" * 64))
        with self.assertRaisesRegex(CatalogStoreError, "changed bytes"):
            self.store.refresh("php")

    def test_missing_cache_falls_back_without_resetting_revision(self) -> None:
        self.publish("php", "php-valid.json")
        self.store.refresh("php")
        digest = self.store._metadata("php")["sha256"]
        self.store._cache_path("php", digest).unlink()
        selected = self.store.effective("php", bundled=(Path(__file__).parents[1] / "resources/artifacts.json",))
        self.assertEqual("bundled", selected.source)
        self.assertIn("missing", selected.warning)
        self.publish("php", "php-older.json")
        with self.assertRaisesRegex(CatalogStoreError, "older than accepted"):
            self.store.refresh("php")

    def test_local_override_is_not_replaced_by_refresh(self) -> None:
        override = self.paths.config / "node-artifacts.json"
        original = (Path(__file__).parents[1] / "resources/node-artifacts.json").read_bytes()
        override.write_bytes(original)
        self.publish("node", "node-valid.json")
        self.store.refresh("node")
        self.assertEqual("local override", self.store.effective("node").source)
        self.assertEqual(original, override.read_bytes())

    def test_wrong_signer_is_rejected(self) -> None:
        self.publish("node", "node-valid.json")
        self.store.fingerprint = "0" * 40
        with self.assertRaisesRegex(CatalogStoreError, "pinned release key"):
            self.store.refresh("node")

    def test_signed_but_invalid_schema_does_not_replace_cache(self) -> None:
        self.publish("php", "php-valid.json")
        self.store.refresh("php")
        self.publish("php", "php-invalid.json")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            self.store.refresh("php")
        self.assertEqual(2, self.store.effective("php").revision)

    def test_corrupt_cached_signature_falls_back_with_warning(self) -> None:
        self.publish("php", "php-valid.json")
        self.store.refresh("php")
        digest = self.store._metadata("php")["sha256"]
        self.store._cache_path("php", digest).with_suffix(".json.sig").write_bytes(b"wrong")
        selected = self.store.effective("php")
        self.assertEqual("bundled", selected.source)
        self.assertIn("signature", selected.warning)

    def test_expired_signature_status_is_rejected_even_with_validsig(self) -> None:
        self.publish("php", "php-valid.json")
        valid = f"[GNUPG:] VALIDSIG {self.fingerprint} 0 0 0 0 0 0 0 {self.fingerprint}\n"
        with mock.patch("paddock.catalog_store.subprocess.run") as runner:
            runner.side_effect = [
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, valid + "[GNUPG:] EXPKEYSIG bad\n", ""),
            ]
            with self.assertRaisesRegex(CatalogStoreError, "signature"):
                self.store.refresh("php")

    def test_interrupted_state_write_keeps_previous_revision(self) -> None:
        self.publish("php", "php-older.json")
        self.store.refresh("php")
        previous = self.store._metadata("php")
        self.publish("php", "php-valid.json")
        from paddock import catalog_store
        original = catalog_store.atomic_write

        def interrupted(path, payload):
            if path == self.store._state_path("php"):
                raise OSError("simulated interruption")
            return original(path, payload)

        with mock.patch("paddock.catalog_store.atomic_write", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "simulated interruption"):
                self.store.refresh("php")
        self.assertEqual(previous, self.store._metadata("php"))
        self.assertEqual(1, self.store.effective("php").revision)

    def test_concurrent_refreshes_preserve_one_valid_revision(self) -> None:
        self.publish("node", "node-valid.json")
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.store.refresh, ("node", "node")))
        self.assertEqual([2, 2], [result.revision for result in results])
        self.assertEqual(2, self.store.effective("node").revision)


if __name__ == "__main__":
    unittest.main()
