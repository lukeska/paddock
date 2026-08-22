from __future__ import annotations

import configparser
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).parents[1]
RESOURCES = ROOT / "resources"
APP_ID = "dev.paddock.Paddock"


class DesktopIntegrationTests(unittest.TestCase):
    def test_desktop_entry_launches_the_native_app(self) -> None:
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(RESOURCES / f"{APP_ID}.desktop", encoding="utf-8")
        entry = parser["Desktop Entry"]
        self.assertEqual("Application", entry["Type"])
        self.assertEqual("paddock-ui", entry["Exec"])
        self.assertEqual(APP_ID, entry["Icon"])
        self.assertEqual("false", entry["Terminal"])
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

    def test_package_installs_all_desktop_resources(self) -> None:
        package = (ROOT / "packaging/arch/PKGBUILD").read_text(encoding="utf-8")
        for suffix in (".desktop", ".metainfo.xml", ".svg", "-symbolic.svg"):
            self.assertIn(f"{APP_ID}{suffix}", package)

    def test_plugin_launches_ui_with_an_unavailable_fallback(self) -> None:
        panel = (ROOT / "plugin/Panel.qml").read_text(encoding="utf-8")
        self.assertIn("command -v paddock-ui", panel)
        self.assertIn("exec paddock-ui", panel)
        self.assertIn("Paddock UI unavailable", panel)


if __name__ == "__main__":
    unittest.main()
