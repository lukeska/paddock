package tui

import (
	"image/color"

	"charm.land/lipgloss/v2"

	"github.com/lukeska/paddock/internal/backend"
)

type styles struct {
	brand, activeTab, tab, section, selected, worker lipgloss.Style
	muted, accent, good, warn, error, log            lipgloss.Style
}

func newStyles(dark bool, palette *backend.ThemePalette) styles {
	if palette != nil {
		dark = palette.Mode == "dark"
	}
	choose := lipgloss.LightDark(dark)
	accent := choose(lipgloss.Color("#8f3f71"), lipgloss.Color("#d3869b"))
	muted := choose(lipgloss.Color("#6c6f85"), lipgloss.Color("#928374"))
	good := choose(lipgloss.Color("#427b58"), lipgloss.Color("#b8bb26"))
	warn := choose(lipgloss.Color("#b57614"), lipgloss.Color("#fabd2f"))
	errorColor := choose(lipgloss.Color("#9d0006"), lipgloss.Color("#fb4934"))
	var foreground color.Color
	var selection color.Color
	if palette != nil {
		accent = paletteColor(palette.Accent, accent)
		muted = paletteColor(palette.Muted, muted)
		good = paletteColor(palette.Green, good)
		warn = paletteColor(firstColor(palette.Orange, palette.Yellow), warn)
		errorColor = paletteColor(palette.Red, errorColor)
		foreground = paletteColor(palette.Foreground, nil)
		selection = paletteColor(palette.Selection, nil)
	}
	selected := lipgloss.NewStyle().Bold(true)
	if selection != nil {
		selected = selected.Background(selection)
	}
	if foreground != nil {
		selected = selected.Foreground(foreground)
	}
	log := lipgloss.NewStyle().Foreground(choose(
		lipgloss.Color("#3c3836"), lipgloss.Color("#d5c4a1"),
	))
	if foreground != nil {
		log = log.Foreground(foreground)
	}
	return styles{
		brand:     lipgloss.NewStyle().Bold(true).Foreground(accent),
		activeTab: lipgloss.NewStyle().Bold(true).Underline(true).Foreground(accent),
		tab:       lipgloss.NewStyle().Foreground(muted),
		section:   lipgloss.NewStyle().Bold(true).Foreground(accent),
		selected:  selected,
		worker:    lipgloss.NewStyle().Foreground(accent),
		muted:     lipgloss.NewStyle().Foreground(muted),
		accent:    lipgloss.NewStyle().Foreground(accent),
		good:      lipgloss.NewStyle().Foreground(good),
		warn:      lipgloss.NewStyle().Foreground(warn),
		error:     lipgloss.NewStyle().Bold(true).Foreground(errorColor),
		log:       log,
	}
}

func paletteColor(value *string, fallback color.Color) color.Color {
	if value == nil {
		return fallback
	}
	return lipgloss.Color(*value)
}

func firstColor(values ...*string) *string {
	for _, value := range values {
		if value != nil {
			return value
		}
	}
	return nil
}
