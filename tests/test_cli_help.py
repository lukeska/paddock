"""`paddock help` is the discoverability surface for the whole CLI.

argparse alone produced a bare `usage:` line naming fifteen choices with no
descriptions, and it cannot document `php list`/`php use` at all: `php`
forwards everything after itself through `nargs=REMAINDER`, so even
`paddock php --help` reaches PHP instead of argparse. The grouped table in
`cli.OVERVIEW` fills both gaps, and these tests keep it honest.
"""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from paddock import cli


def invoke(*argv: str) -> tuple[int, str]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = cli.run(list(argv))
    return code, output.getvalue()


def registered() -> set[str]:
    return set(cli.build()[1])


def listed() -> set[str]:
    # "php use VERSION" and "link [NAME]" both name their command first.
    return {
        invocation.split()[0]
        for _, entries in cli.OVERVIEW
        for invocation, _ in entries
    }


class OverviewCoverageTests(unittest.TestCase):
    def test_every_command_is_listed(self) -> None:
        missing = registered() - listed()
        self.assertEqual(
            set(), missing,
            f"add {sorted(missing)} to cli.OVERVIEW: a command absent from the "
            "table is undiscoverable, since the flat choices block is suppressed.",
        )

    def test_nothing_listed_is_unimplemented(self) -> None:
        self.assertEqual(set(), listed() - registered())

    def test_every_entry_describes_itself(self) -> None:
        for _, entries in cli.OVERVIEW:
            for invocation, description in entries:
                self.assertTrue(description.strip(), invocation)

    def test_every_command_carries_a_description(self) -> None:
        # `paddock help COMMAND` prints this; an empty one is a blank page.
        for name, parser in cli.build()[1].items():
            self.assertTrue((parser.description or "").strip(), name)


class HelpOutputTests(unittest.TestCase):
    def test_help_exits_zero_and_names_every_command(self) -> None:
        code, output = invoke("help")
        self.assertEqual(0, code)
        for command in registered():
            self.assertIn(command, output)

    def test_help_groups_commands_by_task(self) -> None:
        _, output = invoke("help")
        for group, _ in cli.OVERVIEW:
            self.assertIn(f"{group}:", output)

    def test_a_bare_invocation_prints_the_list(self) -> None:
        # Exit 2 is argparse's existing answer to a missing command; only the
        # output improves, so nothing that scripts a bare call changes.
        code, output = invoke()
        self.assertEqual(2, code)
        self.assertIn("paddock link", output)

    def test_help_explains_one_command(self) -> None:
        code, output = invoke("help", "secure")
        self.assertEqual(0, code)
        self.assertIn("locally trusted certificate", output)

    def test_help_reaches_commands_argparse_cannot(self) -> None:
        # `paddock php --help` is swallowed by REMAINDER, so this is the only
        # route to the runtime subcommands.
        _, output = invoke("help", "php")
        for verb in ("php list", "php install", "php remove", "php use", "php -- "):
            self.assertIn(verb, output)

    def test_an_unknown_topic_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as caught:
            invoke("help", "nope")
        self.assertIn("nope", str(caught.exception))

    def test_documented_exit_codes_match_the_implementation(self) -> None:
        # doctor and status return nonzero on failure; help says which.
        commands = cli.build()[1]
        self.assertIn("1", commands["doctor"].epilog or "")
        self.assertIn("3", commands["status"].epilog or "")


class ServiceMessageTests(unittest.TestCase):
    """`"stop".capitalize() + "ed"` spells "Stoped"."""

    def test_each_action_has_a_real_past_tense(self) -> None:
        commands = cli.build()[1]
        actions = commands["service"]._actions
        choices = next(a.choices for a in actions if a.dest == "action")
        for action in ("start", "stop", "restart"):
            self.assertIn(action, choices)
        self.assertEqual("Stopped", cli.ACTION_DONE["stop"])
        self.assertEqual("Started", cli.ACTION_DONE["start"])
        self.assertEqual("Restarted", cli.ACTION_DONE["restart"])

    def test_every_controllable_action_can_be_reported(self) -> None:
        # A new action without a past tense would raise KeyError at runtime.
        commands = cli.build()[1]
        choices = next(
            a.choices for a in commands["service"]._actions if a.dest == "action"
        )
        for action in choices:
            if action in {"add", "logs", "remove"}:
                continue
            self.assertIn(action, cli.ACTION_DONE, action)


class HelpWithoutStateTests(unittest.TestCase):
    """Help must answer when the state directories cannot be created.

    A user reaching for `paddock help` may be doing so precisely because
    something is broken, so it has to resolve before the store is touched.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        blocked = Path(self.temporary.name) / "not-a-directory"
        blocked.write_text("", encoding="utf-8")
        self.environment = mock.patch.dict(
            os.environ,
            {
                "HOME": str(blocked),
                "XDG_CONFIG_HOME": str(blocked),
                "XDG_DATA_HOME": str(blocked),
                "XDG_STATE_HOME": str(blocked),
                "XDG_CACHE_HOME": str(blocked),
            },
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_help_answers_anyway(self) -> None:
        with self.environment:
            code, output = invoke("help")
        self.assertEqual(0, code)
        self.assertIn("paddock doctor", output)

    def test_a_state_touching_command_still_fails_there(self) -> None:
        # Proves the fixture really is broken, so the test above means
        # something.
        with self.environment, self.assertRaises(OSError):
            invoke("php", "list")


if __name__ == "__main__":
    unittest.main()
