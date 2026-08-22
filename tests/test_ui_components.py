from __future__ import annotations

import unittest

from paddock.ui.components import design_css


class DesignCssTests(unittest.TestCase):
    def test_design_uses_semantic_theme_variables(self) -> None:
        css = design_css()
        for variable in (
            "--card-bg-color",
            "--border-color",
            "--accent-color",
            "--error-bg-color",
        ):
            self.assertIn(f"var({variable})", css)

    def test_design_does_not_freeze_theme_colors(self) -> None:
        self.assertNotRegex(design_css(), r"#[0-9a-fA-F]{6}")

    def test_omarchy_inspired_components_have_stable_css_hooks(self) -> None:
        css = design_css()
        for name in ("paddock-hero", "paddock-card", "paddock-section-title"):
            self.assertIn(f".{name}", css)


if __name__ == "__main__":
    unittest.main()
