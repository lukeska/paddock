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

    def test_service_leds_use_semantic_success_and_error_colors(self) -> None:
        css = design_css()
        self.assertIn(".paddock-led-active", css)
        self.assertIn("var(--success-bg-color)", css)
        self.assertIn(".paddock-led-inactive", css)
        self.assertIn("var(--error-bg-color)", css)

    def test_buttons_are_compact_outlined_and_square(self) -> None:
        css = design_css()
        self.assertIn(".paddock-shell button", css)
        self.assertIn(".paddock-dialog button", css)
        self.assertIn("font-size: 0.82em", css)
        self.assertIn("border: 1px solid var(--border-color)", css)
        self.assertIn("border-radius: 0", css)
        self.assertIn("background-color: transparent", css)

    def test_text_links_override_the_global_button_outline(self) -> None:
        css = design_css()
        self.assertIn("button.paddock-text-link", css)
        self.assertIn("border: 0", css)
        self.assertIn("text-decoration-line: underline", css)

    def test_section_cards_are_square(self) -> None:
        css = design_css()
        self.assertIn(".paddock-card { padding: 2px; border-radius: 0; }", css)

    def test_card_row_styling_does_not_leak_into_dropdown_popovers(self) -> None:
        css = design_css()
        self.assertIn(".paddock-card > row", css)
        self.assertNotIn(".paddock-card row {", css)
        self.assertIn("background-color: var(--popover-bg-color)", css)

    def test_service_rows_are_compact(self) -> None:
        css = design_css()
        self.assertIn(".paddock-service-row", css)
        self.assertIn("min-height: 32px", css)
        self.assertIn("font-size: 0.9em", css)

    def test_selected_sidebar_page_is_square(self) -> None:
        css = design_css()
        self.assertIn(
            ".paddock-shell .navigation-sidebar row:selected { border-radius: 0; }",
            css,
        )

    def test_environment_blocks_are_compact_and_monospace(self) -> None:
        css = design_css()
        self.assertIn(".paddock-env-block", css)
        self.assertIn("font-family: monospace", css)

    def test_sites_search_rail_spans_with_theme_colors(self) -> None:
        css = design_css()
        self.assertIn(".paddock-sites-rail", css)
        self.assertIn("background-color: var(--headerbar-bg-color)", css)


if __name__ == "__main__":
    unittest.main()
