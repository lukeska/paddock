from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


INDEX_PATH = Path(__file__).parents[1] / "release/php/index.py"
SPEC = importlib.util.spec_from_file_location("paddock_release_index", INDEX_PATH)
assert SPEC and SPEC.loader
index = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(index)


class ReleaseIndexTests(unittest.TestCase):
    def test_generates_strict_https_public_index_and_ignores_unknown_files(self):
        with tempfile.TemporaryDirectory() as temporary_name:
            directory = Path(temporary_name)
            artifact = directory / "paddock-php-8.4.23-linux-x86_64.tar.gz"
            artifact.write_bytes(b"artifact")
            (directory / "unrelated.tar.gz").write_bytes(b"ignore")
            output = directory / "index.json"
            with patch.object(
                sys,
                "argv",
                [
                    "index.py", "--dist", str(directory), "--output", str(output),
                    "--base-url", "https://releases.example/paddock",
                ],
            ):
                index.main()
            value = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(value["schema_version"], 1)
            self.assertEqual(len(value["artifacts"]), 1)
            self.assertEqual(value["artifacts"][0]["minor"], "8.4")
            self.assertEqual(
                value["artifacts"][0]["url"],
                "https://releases.example/paddock/" + artifact.name,
            )


if __name__ == "__main__":
    unittest.main()
