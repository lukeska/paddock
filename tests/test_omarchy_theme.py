from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from paddock.omarchy_theme import ThemeError, load_palette


DARK = """\
mode = "dark"
accent = "#FAA968"
muted = "#2a6b78"
background = "#05182e"
dark_background = "#031222"
darker_background = "#020c17"
lighter_background = "#0a2540"
foreground = "#f6dcac"
red = "#f85525"
yellow = "#e97b3c"
orange = "#faa968"
green = "#028391"
cyan = "#8cbfb8"
blue = "#3f8f8a"
"""


class ThemeFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "colors.toml"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, value: str) -> None:
        self.path.write_text(value)


class PaletteTests(ThemeFixture):
    def test_dark_palette_is_normalized(self) -> None:
        self.write(DARK)
        palette = load_palette(self.path)
        self.assertEqual("dark", palette.mode)
        self.assertEqual("#faa968", palette.accent)

    def test_light_partial_palette_is_valid(self) -> None:
        self.write('mode = "light"\naccent = "#336699"\n')
        palette = load_palette(self.path)
        self.assertEqual("light", palette.mode)
        self.assertIsNone(palette.background)

    def test_invalid_mode_is_rejected(self) -> None:
        self.write('mode = "sepia"\n')
        with self.assertRaises(ThemeError):
            load_palette(self.path)

    def test_non_hex_color_is_rejected(self) -> None:
        self.write('mode = "dark"\naccent = "red; } button { opacity: 0"\n')
        with self.assertRaises(ThemeError):
            load_palette(self.path)

    def test_missing_and_malformed_files_have_actionable_errors(self) -> None:
        with self.assertRaises(ThemeError) as missing:
            load_palette(self.path)
        self.assertIn(str(self.path), str(missing.exception))
        self.write("not = [toml")
        with self.assertRaises(ThemeError):
            load_palette(self.path)


if __name__ == "__main__":
    unittest.main()
