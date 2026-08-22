"""The Omarchy plugin's manifest, checked without Omarchy installed.

`omarchy plugin validate` is the real gate and exits 0 exactly when the shell
will accept a plugin, but CI runs in a bare Arch container with no Omarchy, so
these rules are reimplemented from `/usr/share/omarchy/shell/README.md` and
`services/PluginRegistry.qml`. They are the cheap half; the acceptance suite
runs the real validator when it can.

Note what this cannot catch: the validator reads the manifest only. A QML error
still loads a broken widget into the bar, which is why the plugin also has to
be looked at on a live shell.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import unittest


PLUGIN = Path(__file__).parents[1] / "plugin"
# PluginRegistry.qml: ids are matched against this and `omarchy.` is refused.
PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
RESERVED = "omarchy."
KIND_ENTRY_POINTS = {
    "bar-widget": "barWidget",
    "panel": "panel",
    "overlay": "overlay",
    "menu": "menu",
    "service": "service",
    "bar": "bar",
}


def manifest() -> dict:
    return json.loads((PLUGIN / "manifest.json").read_text(encoding="utf-8"))


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = manifest()

    def test_schema_version_is_the_number_one(self) -> None:
        # The string "1" is rejected by the shell; JSON makes that easy to get
        # wrong and impossible to see by eye.
        self.assertIsInstance(self.manifest["schemaVersion"], int)
        self.assertNotIsInstance(self.manifest["schemaVersion"], bool)
        self.assertEqual(1, self.manifest["schemaVersion"])

    def test_required_fields_are_present(self) -> None:
        for field in ("id", "name", "version", "kinds", "entryPoints"):
            self.assertIn(field, self.manifest)
            self.assertTrue(self.manifest[field], field)

    def test_the_id_is_well_formed_and_not_reserved(self) -> None:
        plugin_id = self.manifest["id"]
        self.assertRegex(plugin_id, PLUGIN_ID)
        # Third-party plugins may never shadow a first-party one.
        self.assertFalse(plugin_id.startswith(RESERVED), plugin_id)

    def test_every_kind_declares_its_entry_point(self) -> None:
        for kind in self.manifest["kinds"]:
            self.assertIn(kind, KIND_ENTRY_POINTS, kind)
            self.assertIn(KIND_ENTRY_POINTS[kind], self.manifest["entryPoints"], kind)

    def test_entry_points_are_relative_contained_and_present(self) -> None:
        for key, value in self.manifest["entryPoints"].items():
            self.assertFalse(value.startswith("/"), key)
            self.assertNotIn("..", value, key)
            self.assertTrue((PLUGIN / value).is_file(), f"{key} -> {value} is missing")

    def test_no_entry_point_is_orphaned(self) -> None:
        # An entry point with no matching kind is dead weight the shell ignores.
        expected = {KIND_ENTRY_POINTS[kind] for kind in self.manifest["kinds"]}
        self.assertEqual(expected, set(self.manifest["entryPoints"]))

    def test_the_plugin_folder_holds_no_symlink(self) -> None:
        # The registry refuses a plugin containing any symlink, so the dev
        # install script copies rather than links.
        for path in PLUGIN.rglob("*"):
            self.assertFalse(path.is_symlink(), path)

    def test_the_widget_settings_schema_matches_its_defaults(self) -> None:
        widget = self.manifest["barWidget"]
        defaults = widget.get("defaults", {})
        for entry in widget.get("schema", []):
            self.assertIn(entry["key"], defaults, entry["key"])
            self.assertEqual(defaults[entry["key"]], entry["defaultValue"], entry["key"])
            if "min" in entry:
                self.assertGreaterEqual(entry["defaultValue"], entry["min"])
                self.assertLessEqual(entry["defaultValue"], entry["max"])

    def test_the_service_owns_the_polling(self) -> None:
        # A bar widget exists once per monitor; polling there would multiply
        # the subprocess count by the number of screens.
        self.assertIn("service", self.manifest["kinds"])
        service = (PLUGIN / self.manifest["entryPoints"]["service"]).read_text(encoding="utf-8")
        self.assertIn("Timer", service)
        self.assertIn("paddock", service)
        widget = (PLUGIN / self.manifest["entryPoints"]["barWidget"]).read_text(encoding="utf-8")
        self.assertNotIn("Timer", widget)

    def test_the_widget_reads_its_service_through_the_supported_route(self) -> None:
        # Bar widgets are not given `service` directly; only panel entries are.
        widget = (PLUGIN / self.manifest["entryPoints"]["barWidget"]).read_text(encoding="utf-8")
        self.assertIn("serviceFor", widget)

    def test_every_loaded_qml_file_exists(self) -> None:
        # The panel is loaded by Qt.resolvedUrl, not by an entry point, so the
        # manifest cannot catch a typo here. A missing source leaves the Loader
        # null, `open()` silently does nothing, and summon still reports ok.
        widget = (PLUGIN / self.manifest["entryPoints"]["barWidget"]).read_text(encoding="utf-8")
        for referenced in re.findall(r'Qt\.resolvedUrl\("([^"]+)"\)', widget):
            self.assertTrue((PLUGIN / referenced).is_file(), referenced)

    def test_the_widget_satisfies_the_summon_contract(self) -> None:
        # Bar.findPanelWidget skips any widget without all three, and the only
        # symptom is a "no live bar widget" warning.
        widget = (PLUGIN / self.manifest["entryPoints"]["barWidget"]).read_text(encoding="utf-8")
        for member in ("function open(", "function close(", "property bool opened"):
            self.assertIn(member, widget, member)

    def test_only_one_component_owns_the_ipc_target(self) -> None:
        # The shell refuses a second handler for the same target, so the panel
        # sets manageIpc false and leaves the target to the service.
        owners = [
            path.name for path in PLUGIN.glob("*.qml")
            if 'target: "dev.paddock.status"' in path.read_text(encoding="utf-8")
            and "manageIpc: false" not in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(["Service.qml"], owners)

    def test_colours_come_from_the_theme_singletons(self) -> None:
        # Bound colours re-theme live when omarchy-theme-set pushes a palette;
        # a literal would freeze at whatever the theme was when it was written.
        for name in ("BarWidget.qml", "Panel.qml"):
            source = (PLUGIN / name).read_text(encoding="utf-8")
            self.assertIn("Color.", source, name)
            self.assertNotRegex(source, r'"#[0-9a-fA-F]{6}"', name)


class RetiredNameTests(unittest.TestCase):
    """The bar's only glyph was a literal "L" left over from the old name."""

    def test_the_retired_project_name_appears_nowhere(self) -> None:
        # Tracked files only: build output under release/dist still carries the
        # old name in artefact filenames and is gitignored, so walking the
        # filesystem would fail on files nobody publishes.
        retired = "lara" + "machy"          # assembled so this file passes
        root = Path(__file__).parents[1]
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            text=True, capture_output=True, check=True,
        )
        offenders = []
        for name in listing.stdout.split("\0"):
            if not name:
                continue
            path = root / name
            if not path.is_file() or path.suffix in {".png", ".gz", ".zst"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if retired in text.lower() or retired in name.lower():
                offenders.append(name)
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
