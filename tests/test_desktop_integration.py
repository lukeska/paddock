from __future__ import annotations

import configparser
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).parents[1]
RESOURCES = ROOT / "resources"
APP_ID = "dev.paddock.Paddock"


class DesktopIntegrationTests(unittest.TestCase):
    def test_desktop_entry_launches_the_tui_in_a_terminal(self) -> None:
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(RESOURCES / f"{APP_ID}.desktop", encoding="utf-8")
        entry = parser["Desktop Entry"]
        self.assertEqual("Application", entry["Type"])
        self.assertEqual("paddock tui", entry["Exec"])
        self.assertEqual(APP_ID, entry["Icon"])
        self.assertEqual("true", entry["Terminal"])
        self.assertIn("Development;", entry["Categories"])

    def test_appstream_and_desktop_ids_match(self) -> None:
        component = ET.parse(RESOURCES / f"{APP_ID}.metainfo.xml").getroot()
        self.assertEqual(APP_ID, component.findtext("id"))
        launchable = component.find("launchable")
        self.assertIsNotNone(launchable)
        self.assertEqual(f"{APP_ID}.desktop", launchable.text)
        self.assertEqual("desktop-id", launchable.attrib["type"])

    def test_both_icon_variants_are_present(self) -> None:
        for suffix in (".svg", "-symbolic.svg"):
            icon = RESOURCES / f"{APP_ID}{suffix}"
            self.assertTrue(icon.is_file())
            self.assertEqual("svg", ET.parse(icon).getroot().tag.rsplit("}", 1)[-1])

    @unittest.skipUnless(
        (ROOT / "packaging/arch/PKGBUILD").is_file(),
        "the release source archive intentionally excludes PKGBUILD",
    )
    def test_package_installs_the_tui_without_gtk(self) -> None:
        package = (ROOT / "packaging/arch/PKGBUILD").read_text(encoding="utf-8")
        self.assertIn('makedepends=(\'go\')', package)
        self.assertIn('paddock-tui "$pkgdir/usr/bin/paddock-tui"', package)
        self.assertIn('-type d -empty -delete', package)
        self.assertIn('go test ./...', package)
        self.assertIn("resources/composer.json", package)
        self.assertIn('shims/php', package)
        self.assertIn('shims/node', package)
        self.assertIn('shims/npm', package)
        self.assertIn('shims/npx', package)
        self.assertIn('node-artifacts.json', package)
        self.assertIn('shims/composer', package)
        self.assertIn('shims/laravel', package)
        self.assertNotIn("paddock-ui", package)
        for dependency in ("python-gobject", "gtk4", "libadwaita"):
            self.assertNotIn(dependency, package)
        for suffix in (".desktop", ".metainfo.xml", ".svg", "-symbolic.svg"):
            self.assertIn(f"{APP_ID}{suffix}", package)

    def test_plugin_launches_the_tui_in_an_omarchy_terminal(self) -> None:
        panel = (ROOT / "plugin/Panel.qml").read_text(encoding="utf-8")
        self.assertIn('root.inTerminal("paddock tui")', panel)
        self.assertNotIn("paddock-ui", panel)

    def test_plugin_dev_installer_has_no_gui_override(self) -> None:
        installer = (ROOT / "scripts/plugin-dev-install.sh").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("paddock-ui", installer)

    def test_python_gui_package_is_removed(self) -> None:
        self.assertEqual([], list((ROOT / "src/paddock/ui").glob("*.py")))
        self.assertFalse((ROOT / "packaging/arch/paddock-ui").exists())


if __name__ == "__main__":
    unittest.main()
