from __future__ import annotations

import unittest
from pathlib import Path

from paddock.ui.app import (
    error_clipboard_text,
    site_name_matches,
    site_worker_summary,
    terminal_command,
    zed_command,
)


class SiteActionTests(unittest.TestCase):
    def test_site_list_summarizes_worker_state(self) -> None:
        class Site:
            queue_available = False
            queue_configured = False
            queue_state = "not-configured"
            reverb_available = True
            reverb_configured = True
            reverb_state = "active"

        self.assertEqual("Reverb running", site_worker_summary(Site()))
        Site.reverb_state = "failed"
        self.assertEqual("Reverb failed", site_worker_summary(Site()))
        Site.reverb_configured = False
        self.assertEqual("Reverb available", site_worker_summary(Site()))
        Site.reverb_available = False
        self.assertEqual("No workers", site_worker_summary(Site()))
        Site.queue_available = True
        self.assertEqual("Queue available", site_worker_summary(Site()))
        Site.queue_configured = True
        Site.queue_state = "active"
        self.assertEqual("Queue running", site_worker_summary(Site()))

    def test_site_details_expose_reverb_controls_and_logs(self) -> None:
        source = (Path(__file__).parents[1] / "src/paddock/ui/app.py").read_text(
            encoding="utf-8"
        )
        for control in (
            "site_reverb_toggle", "site_reverb_autostart", "site_reverb_logs",
            "site_queue_toggle", "site_queue_autostart", "site_queue_logs",
        ):
            self.assertIn(control, source)

    def test_terminal_uses_omarchys_configured_xdg_terminal_without_a_shell(self) -> None:
        self.assertEqual(
            ["xdg-terminal-exec", "--dir=/home/user/My Laravel App"],
            terminal_command("/home/user/My Laravel App"),
        )

    def test_zed_receives_the_project_path_as_one_argument(self) -> None:
        self.assertEqual(
            ["zeditor", "/home/user/My Laravel App"],
            zed_command("/home/user/My Laravel App"),
        )

    def test_site_search_is_partial_case_insensitive_and_trimmed(self) -> None:
        self.assertTrue(site_name_matches("Laravel-Shop", " shop "))
        self.assertTrue(site_name_matches("Laravel-Shop", "LARAVEL"))
        self.assertFalse(site_name_matches("Laravel-Shop", "api"))

    def test_copied_error_includes_heading_and_full_detail(self) -> None:
        self.assertEqual(
            "Service operation failed\n\nUnit paddock.target was not found.\n",
            error_clipboard_text(
                "Service operation failed", "Unit paddock.target was not found."
            ),
        )


if __name__ == "__main__":
    unittest.main()
