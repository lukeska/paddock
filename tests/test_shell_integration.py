from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from paddock.shell import (
    BLOCK, SHIM_DIRECTORY, ShellIntegrationError,
    install_shell_integration, remove_shell_integration, run_shim,
)


class ShellConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.bashrc = self.home / ".bashrc"
        self.bashrc.write_text("# user config\nexport EDITOR=nvim\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_install_is_idempotent_and_remove_preserves_user_content(self) -> None:
        self.assertTrue(install_shell_integration(self.home))
        self.assertFalse(install_shell_integration(self.home))
        self.assertEqual(1, self.bashrc.read_text(encoding="utf-8").count(BLOCK))
        self.assertTrue(remove_shell_integration(self.home))
        self.assertIn("export EDITOR=nvim", self.bashrc.read_text(encoding="utf-8"))
        self.assertNotIn(str(SHIM_DIRECTORY), self.bashrc.read_text(encoding="utf-8"))

    def test_install_refuses_a_symlinked_bashrc(self) -> None:
        target = self.home / "target"
        target.write_text("untouched", encoding="utf-8")
        self.bashrc.unlink()
        self.bashrc.symlink_to(target)
        with self.assertRaisesRegex(ShellIntegrationError, "symlinked"):
            install_shell_integration(self.home)
        self.assertEqual("untouched", target.read_text(encoding="utf-8"))


class ShimDispatchTests(unittest.TestCase):
    def test_default_context_falls_back_without_recursing_into_the_shim(self) -> None:
        selection = type("Selection", (), {"source": "configured default"})()
        with patch("paddock.shell.select_php", return_value=selection), patch(
            "paddock.shell.shutil.which", return_value="/usr/bin/php"
        ) as which, patch("paddock.shell.os.execvpe") as execute:
            run_shim("php", ["-v"])
        self.assertNotIn(str(SHIM_DIRECTORY), which.call_args.kwargs["path"].split(os.pathsep))
        execute.assert_called_once()

    def test_project_context_dispatches_through_the_paddock_plan(self) -> None:
        selection = type("Selection", (), {"source": "project configuration /tmp/.paddock.json"})()
        plan = unittest.mock.Mock()
        with patch("paddock.shell.select_php", return_value=selection), patch(
            "paddock.shell.plan_php", return_value=plan
        ):
            run_shim("php", ["-v"])
        plan.execute.assert_called_once_with()

    def test_a_broken_project_selection_is_not_hidden_by_system_php(self) -> None:
        from paddock.projects import ProjectError

        with patch(
            "paddock.shell.select_php", side_effect=ProjectError("malformed project selection")
        ), self.assertRaisesRegex(ProjectError, "malformed"):
            run_shim("php", ["-v"])
