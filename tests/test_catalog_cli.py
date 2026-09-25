from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from paddock import cli
from paddock.catalog_store import CatalogSelection


class CatalogCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(os.environ, {"HOME": self.temporary.name,
            "XDG_CONFIG_HOME": self.temporary.name + "/config",
            "XDG_DATA_HOME": self.temporary.name + "/data",
            "XDG_STATE_HOME": self.temporary.name + "/state",
            "XDG_CACHE_HOME": self.temporary.name + "/cache"})
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def test_status_is_read_only_and_names_both_sources(self) -> None:
        with mock.patch("paddock.cli.CatalogStore") as catalog:
            catalog.return_value.status.side_effect = [
                {"source": "refreshed", "revision": 3, "checked_at": "today", "warning": None},
                {"source": "bundled", "revision": None, "checked_at": None, "warning": "offline"},
            ]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, cli.run(["runtimes", "status"]))
        self.assertIn("php\trefreshed\trevision=3", output.getvalue())
        self.assertIn("node\tbundled", output.getvalue())
        self.assertIn("warning: offline", output.getvalue())

    def test_refresh_reports_partial_failure_and_continues(self) -> None:
        with mock.patch("paddock.cli.CatalogStore") as catalog:
            catalog.return_value.refresh.side_effect = [
                RuntimeError("offline"), CatalogSelection(None, "refreshed", 4, "today")
            ]
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(1, cli.run(["runtimes", "refresh"]))
        self.assertIn("php: refresh failed: offline", errors.getvalue())
        self.assertIn("node: accepted revision 4", output.getvalue())
        self.assertEqual([mock.call("php"), mock.call("node")], catalog.return_value.refresh.call_args_list)


if __name__ == "__main__":
    unittest.main()
