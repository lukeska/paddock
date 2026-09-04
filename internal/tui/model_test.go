package tui

import (
	"errors"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/charmbracelet/x/ansi"

	"github.com/lukeska/paddock/internal/backend"
)

type fakeAPI struct{ snapshot backend.Snapshot }

func (f *fakeAPI) Snapshot() (backend.Snapshot, error) { return f.snapshot, nil }
func (f *fakeAPI) SetDashboardActive(bool) (backend.DashboardOperationResult, error) {
	return backend.DashboardOperationResult{}, nil
}
func (f *fakeAPI) SetActive(string, string, bool) (backend.OperationResult, error) {
	return backend.OperationResult{}, nil
}
func (f *fakeAPI) SetAutostart(string, string, bool) (backend.OperationResult, error) {
	return backend.OperationResult{}, nil
}
func (f *fakeAPI) Logs(string, string, int) (backend.LogsResult, error) {
	return backend.LogsResult{}, nil
}
func (f *fakeAPI) Close() error { return nil }

func sampleSnapshot() backend.Snapshot {
	node := "22"
	return backend.Snapshot{
		ProtocolVersion: 1,
		Dashboard: backend.DashboardSnapshot{Services: []backend.DashboardService{{
			Key: "caddy", Title: "Caddy", Group: "web", State: "active", Detail: "Serving sites", Configured: true,
		}}},
		Sites: backend.LinkedSitesSnapshot{Sites: []backend.Site{{
			Name: "linguine", Host: "linguine.test", URL: "https://linguine.test",
			PHP: "8.5", Node: &node, Secured: true, Root: "/srv/linguine",
			QueueAvailable: true, QueueConfigured: true, QueueState: "active", QueueAutostart: true,
			ReverbAvailable: true, ReverbConfigured: true, ReverbState: "inactive",
		}}},
	}
}

func TestStaleSnapshotCannotOverwriteNewerState(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.generation = 3
	updated, _ := m.Update(snapshotMsg{generation: 2, snapshot: sampleSnapshot()})
	result := updated.(Model)
	if result.loaded {
		t.Fatal("stale snapshot was applied")
	}
}

func TestDashboardAndSitesRenderAtBothBreakpoints(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.loaded, m.snapshot, m.width, m.height = true, sampleSnapshot(), 120, 30
	if got := m.render(); !strings.Contains(got, "Caddy") {
		t.Fatalf("dashboard missing service: %q", got)
	}
	m.tab = 1
	wide := m.render()
	for _, expected := range []string{"linguine.test", "PHP 8.5", "Queue", "Reverb"} {
		if !strings.Contains(wide, expected) {
			t.Fatalf("wide sites missing %q: %q", expected, wide)
		}
	}
	m.width = 70
	narrow := m.render()
	if !strings.Contains(narrow, "linguine.test") || !strings.Contains(narrow, "Queue") {
		t.Fatalf("narrow site detail missing: %q", narrow)
	}
}

func TestDashboardSectionsRenderAsResponsiveFieldsets(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.loaded, m.snapshot, m.width = true, sampleSnapshot(), 70
	rendered := ansi.Strip(m.renderDashboard())
	lines := strings.Split(rendered, "\n")
	var top, body, bottom string
	for index, line := range lines {
		if strings.HasPrefix(line, "┌─ Web ") {
			top = line
			body = lines[index+1]
			bottom = lines[index+2]
			break
		}
	}
	if top == "" || !strings.HasPrefix(body, "│ ") || !strings.HasSuffix(body, " │") ||
		!strings.HasPrefix(bottom, "└") || !strings.HasSuffix(bottom, "┘") {
		t.Fatalf("dashboard fieldset was not rendered correctly: %q", rendered)
	}
	if ansi.StringWidth(top) != 62 || ansi.StringWidth(body) != 62 || ansi.StringWidth(bottom) != 62 {
		t.Fatalf("dashboard fieldset is not responsive: %q", rendered)
	}
}

func TestBackendErrorIsShownInline(t *testing.T) {
	m := New("")
	updated, _ := m.Update(connectedMsg{err: errors.New("python missing")})
	result := updated.(Model)
	if got := result.render(); !strings.Contains(got, "python missing") {
		t.Fatalf("error absent: %q", got)
	}
}

func TestOmarchyPaletteReplacesTheFallbackStyles(t *testing.T) {
	accent, selection, foreground := "#7aa2f7", "#292e42", "#a9b1d6"
	palette := &backend.ThemePalette{
		Mode: "dark", Accent: &accent, Selection: &selection, Foreground: &foreground,
	}
	style := newStyles(true, palette)
	accentRendered := style.brand.Render("P")
	selectedRendered := style.selected.Render("site")
	if !strings.Contains(accentRendered, "38;2;122;162;247") {
		t.Fatalf("Tokyo Night accent was not rendered: %q", accentRendered)
	}
	if !strings.Contains(selectedRendered, "48;2;41;46;66") {
		t.Fatalf("Tokyo Night selection was not rendered: %q", selectedRendered)
	}
}

func press(code rune, text string, mod tea.KeyMod) tea.KeyPressMsg {
	return tea.KeyPressMsg(tea.Key{Code: code, Text: text, Mod: mod})
}

func TestTabsOwnTabAndNumberKeysButNotArrows(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	updated, _ := m.handleKey(press(tea.KeyRight, "", 0))
	m = updated.(Model)
	if m.tab != 0 {
		t.Fatal("right arrow changed the active tab")
	}

	updated, _ = m.handleKey(press(tea.KeyTab, "", 0))
	m = updated.(Model)
	if m.tab != 1 {
		t.Fatal("tab did not advance to Sites")
	}

	updated, _ = m.handleKey(press('1', "1", 0))
	m = updated.(Model)
	if m.tab != 0 {
		t.Fatal("1 did not jump to Dashboard")
	}

	updated, _ = m.handleKey(press('2', "2", 0))
	m = updated.(Model)
	if m.tab != 1 {
		t.Fatal("2 did not jump to Sites")
	}

	updated, _ = m.handleKey(press(tea.KeyTab, "", tea.ModShift))
	m = updated.(Model)
	if m.tab != 0 {
		t.Fatal("shift+tab did not move to the previous tab")
	}
}

func TestHorizontalNavigationSelectsAWorkerInsideSites(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.tab, m.worker = 1, 0
	updated, _ := m.handleKey(press(tea.KeyRight, "", 0))
	m = updated.(Model)
	if m.tab != 1 || m.worker != 1 {
		t.Fatal("right did not select Reverb")
	}

	updated, _ = m.handleKey(press(tea.KeyLeft, "", 0))
	m = updated.(Model)
	if m.tab != 1 || m.worker != 0 {
		t.Fatal("left did not select Queue")
	}

	updated, _ = m.handleKey(press('l', "l", 0))
	m = updated.(Model)
	if m.worker != 1 {
		t.Fatal("l did not select Reverb")
	}

	updated, _ = m.handleKey(press('h', "h", 0))
	m = updated.(Model)
	if m.worker != 0 {
		t.Fatal("h did not select Queue")
	}
}

func TestToastHasItsOwnRowAndExpiresWithoutClearingANewerToast(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.tab, m.status, m.toastID = 1, "Stopped queue worker", 4
	footer := ansi.Strip(m.renderFooter())
	lines := strings.Split(footer, "\n")
	if len(lines) < 3 || !strings.Contains(lines[1], "space start/stop") {
		t.Fatalf("instructions do not have their own row: %q", footer)
	}
	if !strings.Contains(lines[2], "Stopped queue worker") {
		t.Fatalf("toast does not have its own row: %q", footer)
	}

	updated, _ := m.Update(toastExpiredMsg(3))
	m = updated.(Model)
	if m.status == "" {
		t.Fatal("an old timer cleared a newer toast")
	}

	updated, _ = m.Update(toastExpiredMsg(4))
	m = updated.(Model)
	if m.status != "" {
		t.Fatal("the current toast did not expire")
	}
}

func TestBusyStateNeverAddsAFlashingWorkingRow(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.loaded, m.snapshot, m.busy = true, sampleSnapshot(), true
	if strings.Contains(ansi.Strip(m.renderFooter()), "Working") {
		t.Fatal("busy state exposed a flashing working row")
	}
}

func TestDashboardShowsAndTogglesStartStopAll(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.loaded, m.snapshot = true, sampleSnapshot()
	if got := ansi.Strip(m.renderDashboard()); !strings.Contains(got, "Stop All") {
		t.Fatalf("active dashboard did not offer Stop All: %q", got)
	}
	m.snapshot.Dashboard.Services[0].State = "inactive"
	if got := ansi.Strip(m.renderDashboard()); !strings.Contains(got, "Start All") {
		t.Fatalf("inactive dashboard did not offer Start All: %q", got)
	}
	updated, command := m.handleKey(press(tea.KeySpace, " ", 0))
	m = updated.(Model)
	if command == nil || !m.busy {
		t.Fatal("space did not begin the dashboard operation")
	}
}

func TestDashboardOperationAnimatesInsideTheControl(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.loaded, m.snapshot = true, sampleSnapshot()
	m.dashboardBusy, m.dashboardGoal, m.spinnerFrame = true, false, 0
	first := ansi.Strip(m.renderDashboard())
	if !strings.Contains(first, "⠋ Stopping All") {
		t.Fatalf("dashboard control did not show its pending operation: %q", first)
	}
	m.generation = 7
	updated, command := m.Update(spinnerTickMsg(7))
	m = updated.(Model)
	if command == nil || m.spinnerFrame != 1 {
		t.Fatal("dashboard spinner did not advance and schedule its next frame")
	}
	if got := ansi.Strip(m.renderDashboard()); !strings.Contains(got, "⠙ Stopping All") {
		t.Fatalf("dashboard spinner frame did not change: %q", got)
	}
}
