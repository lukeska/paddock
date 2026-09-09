package tui

import (
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/charmbracelet/x/ansi"

	"github.com/lukeska/paddock/internal/backend"
)

type fakeAPI struct {
	snapshot backend.Snapshot
	calls    []string
}

func (f *fakeAPI) Snapshot() (backend.Snapshot, error) { return f.snapshot, nil }
func (f *fakeAPI) SetDashboardActive(bool) (backend.DashboardOperationResult, error) {
	return backend.DashboardOperationResult{}, nil
}
func (f *fakeAPI) InstallPHP(minor string) (backend.PHPInstallResult, error) {
	f.calls = append(f.calls, "php-install:"+minor)
	return backend.PHPInstallResult{OK: true, Summary: "Installed PHP " + minor, Snapshot: f.snapshot.PHP}, nil
}
func (f *fakeAPI) InstallNode(major string) (backend.NodeInstallResult, error) {
	f.calls = append(f.calls, "node-install:"+major)
	return backend.NodeInstallResult{OK: true, Summary: "Installed Node.js " + major, Snapshot: f.snapshot.Node}, nil
}
func (f *fakeAPI) AddParkingPath(path string) (backend.ParkingOperationResult, error) {
	f.calls = append(f.calls, "parking-add:"+path)
	return backend.ParkingOperationResult{OK: true, Summary: "Parked " + path, Snapshot: backend.ParkingSnapshot{Paths: []string{path}}}, nil
}
func (f *fakeAPI) RemoveParkingPath(path string) (backend.ParkingOperationResult, error) {
	f.calls = append(f.calls, "parking-remove:"+path)
	return backend.ParkingOperationResult{OK: true, Summary: "Forgot " + path, Snapshot: backend.ParkingSnapshot{}}, nil
}
func (f *fakeAPI) CreateService(kind, label string, port *int, autostart bool) (backend.ServiceOperationResult, error) {
	f.calls = append(f.calls, fmt.Sprintf("service-create:%s:%s:%d:%t", kind, label, *port, autostart))
	return backend.ServiceOperationResult{OK: true}, nil
}
func (f *fakeAPI) SetServiceActive(id string, active bool) (backend.ServiceOperationResult, error) {
	f.calls = append(f.calls, fmt.Sprintf("service-active:%s:%t", id, active))
	return backend.ServiceOperationResult{OK: true}, nil
}
func (f *fakeAPI) UpdateService(id, label string, port int, autostart bool) (backend.ServiceOperationResult, error) {
	f.calls = append(f.calls, fmt.Sprintf("service-update:%s:%s:%d:%t", id, label, port, autostart))
	return backend.ServiceOperationResult{OK: true}, nil
}
func (f *fakeAPI) RemoveService(id string) (backend.ServiceOperationResult, error) {
	f.calls = append(f.calls, "service-remove:"+id)
	return backend.ServiceOperationResult{OK: true}, nil
}
func (f *fakeAPI) ServiceLogs(id string, lines int) (backend.ServiceLogsResult, error) {
	f.calls = append(f.calls, fmt.Sprintf("service-logs:%s:%d", id, lines))
	return backend.ServiceLogsResult{OK: true}, nil
}
func (f *fakeAPI) SetActive(site, worker string, active bool) (backend.OperationResult, error) {
	f.calls = append(f.calls, fmt.Sprintf("active:%s:%s:%t", site, worker, active))
	return backend.OperationResult{}, nil
}
func (f *fakeAPI) SetAutostart(site, worker string, enabled bool) (backend.OperationResult, error) {
	f.calls = append(f.calls, fmt.Sprintf("autostart:%s:%s:%t", site, worker, enabled))
	return backend.OperationResult{}, nil
}
func (f *fakeAPI) SetSitePHP(site, version string) (backend.OperationResult, error) {
	f.calls = append(f.calls, "php:"+site+":"+version)
	return backend.OperationResult{}, nil
}
func (f *fakeAPI) SetSiteNode(site, version string) (backend.OperationResult, error) {
	f.calls = append(f.calls, "node:"+site+":"+version)
	return backend.OperationResult{}, nil
}
func (f *fakeAPI) SetSiteSecured(site string, secured bool) (backend.OperationResult, error) {
	f.calls = append(f.calls, fmt.Sprintf("secured:%s:%t", site, secured))
	return backend.OperationResult{}, nil
}
func (f *fakeAPI) SetSiteConfigurationTrusted(site string, trusted bool) (backend.OperationResult, error) {
	f.calls = append(f.calls, fmt.Sprintf("trusted:%s:%t", site, trusted))
	return backend.OperationResult{OK: true}, nil
}
func (f *fakeAPI) EnsureSiteConfiguration(site string) (backend.OperationResult, error) {
	f.calls = append(f.calls, "ensure:"+site)
	return backend.OperationResult{OK: true}, nil
}
func (f *fakeAPI) ReloadWeb() (backend.DashboardOperationResult, error) {
	f.calls = append(f.calls, "reload-web")
	return backend.DashboardOperationResult{OK: true}, nil
}
func (f *fakeAPI) Logs(site, worker string, lines int) (backend.LogsResult, error) {
	f.calls = append(f.calls, fmt.Sprintf("logs:%s:%s:%d", site, worker, lines))
	return backend.LogsResult{}, nil
}
func (f *fakeAPI) Close() error { return nil }

func sampleSnapshot() backend.Snapshot {
	node := "22"
	projectConfig := ".paddock/nginx.conf"
	return backend.Snapshot{
		ProtocolVersion: backend.ProtocolVersion,
		Dashboard: backend.DashboardSnapshot{Services: []backend.DashboardService{{
			Key: "web", Title: "Web", Group: "web", State: "active", Detail: "Serving sites", Configured: true,
		}}},
		Services: backend.ServiceInstancesSnapshot{Instances: []backend.ServiceInstance{{
			ID: "redis-a1", Type: "redis", Label: "Cache", Image: "docker.io/library/redis:8.10.1",
			Version: "8.10.1", Port: 6379, Volume: "paddock-redis-a1", State: "active", Autostart: true,
			Connection: []string{"REDIS_HOST=127.0.0.1", "REDIS_PORT=6379"},
			Addresses:  []string{"127.0.0.1:6379 → 6379"},
		}}},
		PHP: backend.PHPVersionsSnapshot{Architecture: "x86_64", Versions: []backend.PHPVersion{
			{Minor: "8.5", Release: "8.5.8", Architecture: "x86_64", Available: true},
			{Minor: "8.4", Release: "8.4.23", Architecture: "x86_64", Available: true, Installed: true},
		}},
		Node: backend.NodeVersionsSnapshot{Architecture: "x86_64", Versions: []backend.NodeVersion{
			{Major: "24", Release: "24.8.0", Architecture: "x86_64", Available: true},
			{Major: "22", Release: "22.19.0", Architecture: "x86_64", Available: true, Installed: true},
		}},
		Parking: backend.ParkingSnapshot{Paths: []string{"/home/demo/Code"}},
		Sites: backend.LinkedSitesSnapshot{Sites: []backend.Site{{
			Name: "linguine", Host: "linguine.test", URL: "https://linguine.test",
			PHP: "8.5", Node: &node, Secured: true, Root: "/srv/linguine",
			QueueAvailable: true, QueueConfigured: true, QueueState: "active", QueueAutostart: true,
			ReverbAvailable: true, ReverbConfigured: true, ReverbState: "inactive",
			Type: "laravel", DocumentRoot: "public",
			CustomConfig:  "/home/demo/.config/paddock/nginx/linguine.custom.conf",
			ProjectConfig: &projectConfig, ProjectConfigStatus: "pending",
		}}},
	}
}

func TestPHPPageListsAndInstallsAvailableVersions(t *testing.T) {
	api := &fakeAPI{}
	m := NewWithAPI(api)
	m.loaded, m.snapshot, m.tab, m.width = true, sampleSnapshot(), 3, 80
	api.snapshot = m.snapshot
	rendered := ansi.Strip(m.renderPHP())
	if !strings.Contains(rendered, "PHP 8.5.8") || !strings.Contains(rendered, "[ Install ]") || !strings.Contains(rendered, "✓ Installed") {
		t.Fatalf("PHP runtime states are missing: %q", rendered)
	}
	updated, command := m.installSelectedPHP()
	m = updated.(Model)
	if !m.phpBusy || command == nil {
		t.Fatal("available PHP did not start installation")
	}
	message := executeCommand(command)
	if message == nil || len(api.calls) == 0 || api.calls[len(api.calls)-1] != "php-install:8.5" {
		t.Fatalf("PHP install calls = %v", api.calls)
	}

	m.phpCursor = 1
	_, command = m.installSelectedPHP()
	if command != nil {
		t.Fatal("installed PHP offered another installation")
	}
}

func TestNodePageListsAndInstallsAvailableVersions(t *testing.T) {
	api := &fakeAPI{}
	m := NewWithAPI(api)
	m.loaded, m.snapshot, m.tab, m.width = true, sampleSnapshot(), 4, 80
	api.snapshot = m.snapshot
	rendered := ansi.Strip(m.renderNode())
	if !strings.Contains(rendered, "Node.js 24.8.0") || !strings.Contains(rendered, "[ Install ]") || !strings.Contains(rendered, "✓ Installed") {
		t.Fatalf("Node.js runtime states are missing: %q", rendered)
	}
	updated, command := m.installSelectedNode()
	m = updated.(Model)
	if !m.nodeBusy || command == nil {
		t.Fatal("available Node.js did not start installation")
	}
	executeCommand(command)
	if len(api.calls) == 0 || api.calls[len(api.calls)-1] != "node-install:24" {
		t.Fatalf("Node.js install calls = %v", api.calls)
	}

	m.nodeCursor = 1
	_, command = m.installSelectedNode()
	if command != nil {
		t.Fatal("installed Node.js offered another installation")
	}
}

func TestParkingPageAddsAndRemovesWithoutDeletingTheFolder(t *testing.T) {
	api := &fakeAPI{}
	m := NewWithAPI(api)
	m.loaded, m.snapshot, m.tab, m.width = true, sampleSnapshot(), 5, 80
	api.snapshot = m.snapshot
	if rendered := ansi.Strip(m.renderParking()); !strings.Contains(rendered, "/home/demo/Code") || !strings.Contains(rendered, "Add Folder") {
		t.Fatalf("parking page is incomplete: %q", rendered)
	}

	m.parkingForm, m.parkingPath = true, "/home/demo/Projects"
	updated, command := m.addParkingPath()
	m = updated.(Model)
	if !m.parkingBusy || command == nil {
		t.Fatal("parking add did not start")
	}
	executeCommand(command)
	if api.calls[len(api.calls)-1] != "parking-add:/home/demo/Projects" {
		t.Fatalf("parking add calls = %v", api.calls)
	}

	m.busy, m.parkingBusy, m.parkingForm, m.parkingCursor = false, false, false, 0
	updated, command = m.removeSelectedParkingPath()
	if command == nil {
		t.Fatal("parking removal did not start")
	}
	executeCommand(command)
	if api.calls[len(api.calls)-1] != "parking-remove:/home/demo/Code" {
		t.Fatalf("parking remove calls = %v", api.calls)
	}
	m = updated.(Model)
	m.parkingConfirm = true
	if rendered := ansi.Strip(m.renderParking()); !strings.Contains(rendered, "remain on disk") {
		t.Fatalf("removal confirmation does not explain preservation: %q", rendered)
	}
}

func TestParkingPathTabCompletionOnlyOffersDirectories(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{"alpha", "alpine", "beta"} {
		if err := os.Mkdir(root+"/"+name, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(root+"/also-a-file", []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	matches, err := directoryCompletions(root + "/al")
	if err != nil {
		t.Fatal(err)
	}
	if len(matches) != 2 || strings.Contains(strings.Join(matches, " "), "also-a-file") {
		t.Fatalf("directory matches = %v", matches)
	}
	if prefix := commonPathPrefix(matches); prefix != root+"/alp" {
		t.Fatalf("common prefix = %q", prefix)
	}

	m := NewWithAPI(&fakeAPI{})
	m.parkingForm, m.parkingPath = true, root+"/bet"
	updated, _ := m.handleKey(press(tea.KeyTab, "tab", 0))
	if path := updated.(Model).parkingPath; path != root+"/beta/" {
		t.Fatalf("tab completed path to %q", path)
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
	if got := m.render(); !strings.Contains(got, "Web") {
		t.Fatalf("dashboard missing service: %q", got)
	}
	m.tab = 1
	wide := m.render()
	for _, expected := range []string{"Name", "PHP", "Node", "HTTP(S)", "linguine", "8.5", "22", "HTTPS", "Open"} {
		if !strings.Contains(wide, expected) {
			t.Fatalf("wide sites missing %q: %q", expected, wide)
		}
	}
	m.width = 70
	narrow := m.render()
	if !strings.Contains(narrow, "linguine") || !strings.Contains(narrow, "Open") {
		t.Fatalf("narrow site detail missing: %q", narrow)
	}
}

func TestSelectedSiteRowCoversTheFullTableWidth(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.loaded, m.snapshot, m.tab, m.width, m.height = true, sampleSnapshot(), 1, 80, 24
	lines := strings.Split(m.renderSites(), "\n")
	if len(lines) < 2 {
		t.Fatalf("sites table has no selected row: %q", m.renderSites())
	}
	selected := lines[5]
	if ansi.StringWidth(selected) != 72 {
		t.Fatalf("selected row width = %d, want 72: %q", ansi.StringWidth(selected), ansi.Strip(selected))
	}
	if !strings.HasSuffix(ansi.Strip(selected), "Open") {
		t.Fatalf("selected row does not include the complete Open cell: %q", ansi.Strip(selected))
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

func TestDashboardDoesNotRepeatConfiguredServiceInstances(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.loaded, m.snapshot = true, sampleSnapshot()
	m.snapshot.Dashboard.Services = []backend.DashboardService{{
		Key: "redis-instance", Title: "Cache", Group: "services", State: "active", Configured: true,
	}}
	m.snapshot.Services.Instances = []backend.ServiceInstance{{
		ID: "redis-instance", Label: "Cache", Type: "redis", State: "active", Port: 6379,
	}}
	rendered := ansi.Strip(m.renderDashboard())
	if strings.Count(rendered, "Cache") != 1 {
		t.Fatalf("configured service was rendered more than once: %q", rendered)
	}
	if strings.Contains(rendered, "Service instances") {
		t.Fatalf("redundant service instances section is still present: %q", rendered)
	}
}

func TestServicesTabRendersCatalogAndOpensDetails(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.loaded, m.snapshot, m.tab, m.width = true, sampleSnapshot(), 2, 90
	rendered := ansi.Strip(m.render())
	for _, expected := range []string{"Services", "Add Service", "Cache", "redis", "8.10.1", "6379", "Active"} {
		if !strings.Contains(rendered, expected) {
			t.Fatalf("services page missing %q: %q", expected, rendered)
		}
	}
	updated, _ := m.handleKey(press(tea.KeyEnter, "enter", 0))
	m = updated.(Model)
	if !m.serviceDetail || m.serviceID != "redis-a1" {
		t.Fatal("enter did not open the selected service")
	}
	detail := ansi.Strip(m.renderServiceDetail())
	for _, expected := range []string{"Status", "Configuration", "Connection", "Manage", "Danger zone", "REDIS_HOST=127.0.0.1"} {
		if !strings.Contains(detail, expected) {
			t.Fatalf("service detail missing %q: %q", expected, detail)
		}
	}
}

func TestServiceActionsDispatchAndRemovalRequiresConfirmation(t *testing.T) {
	api := &fakeAPI{}
	m := NewWithAPI(api)
	m.loaded, m.snapshot, m.tab, m.serviceDetail, m.serviceID = true, sampleSnapshot(), 2, true, "redis-a1"
	updated, command := m.activateServiceAction()
	m = updated.(Model)
	if command == nil {
		t.Fatal("start/stop did not create a command")
	}
	executeCommand(command)
	if len(api.calls) != 1 || api.calls[0] != "service-active:redis-a1:false" {
		t.Fatalf("calls = %v", api.calls)
	}

	m.busy, m.serviceAction = false, len(m.serviceActions())-1
	updated, command = m.activateServiceAction()
	m = updated.(Model)
	if command != nil || !m.serviceConfirm {
		t.Fatal("remove did not open confirmation")
	}
	updated, command = m.handleKey(press('y', "y", 0))
	if command == nil {
		t.Fatal("confirmed removal did not create a command")
	}
	executeCommand(command)
	if api.calls[len(api.calls)-1] != "service-remove:redis-a1" {
		t.Fatalf("calls = %v", api.calls)
	}
}

func TestServiceConnectionCanBeCopiedWithoutFlatteningIt(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.loaded, m.snapshot, m.tab, m.serviceDetail, m.serviceID = true, sampleSnapshot(), 2, true, "redis-a1"
	copied := ""
	m.copyText = func(value string) error { copied = value; return nil }
	for index, action := range m.serviceActions() {
		if action.id == "service-env" {
			m.serviceAction = index
			break
		}
	}
	_, command := m.activateServiceAction()
	if command == nil {
		t.Fatal("connection row did not create a copy command")
	}
	updated, _ := m.Update(command())
	m = updated.(Model)
	want := "REDIS_HOST=127.0.0.1\nREDIS_PORT=6379\n"
	if copied != want || m.status != "Copied Cache connection settings" {
		t.Fatalf("copied=%q status=%q", copied, m.status)
	}

	m.snapshot.Services.Instances[0].Connection = nil
	_, command = m.copyServiceConnection(m.snapshot.Services.Instances[0])
	if command != nil || serviceConnectionLabel(nil) != "Not available" {
		t.Fatal("empty connection settings should not offer copying")
	}
}

func TestAddServiceFormSelectsTypeAndValidatesPort(t *testing.T) {
	api := &fakeAPI{}
	m := NewWithAPI(api)
	m.loaded, m.snapshot, m.tab = true, sampleSnapshot(), 2
	m.beginAddService()
	m.cycleServiceType(1)
	if m.serviceLabel != "MySQL" || m.servicePort != "3306" {
		t.Fatalf("type defaults = %q %q", m.serviceLabel, m.servicePort)
	}
	m.serviceLabel, m.servicePort = "App database", "3307"
	updated, command := m.saveServiceForm()
	if command == nil || !updated.(Model).busy {
		t.Fatal("valid add form did not start")
	}
	executeCommand(command)
	if got := api.calls[len(api.calls)-1]; got != "service-create:mysql:App database:3307:true" {
		t.Fatalf("call = %q", got)
	}

	m = updated.(Model)
	m.busy, m.servicePort = false, "80"
	updated, command = m.saveServiceForm()
	if command != nil || !strings.Contains(updated.(Model).err, "1024") {
		t.Fatal("privileged port was accepted")
	}
}

func TestMailpitServiceOffersItsDashboard(t *testing.T) {
	api := &fakeAPI{}
	m := NewWithAPI(api)
	url := "http://127.0.0.1:8025"
	m.snapshot = sampleSnapshot()
	m.snapshot.Services.Instances[0].Type = "mailpit"
	m.snapshot.Services.Instances[0].Label = "Mailpit"
	m.snapshot.Services.Instances[0].Addresses = []string{"127.0.0.1:1025 → 1025", "127.0.0.1:8025 → 8025"}
	m.snapshot.Services.Instances[0].DashboardURL = &url
	m.serviceDetail, m.serviceID = true, "redis-a1"
	opened := ""
	m.openURL = func(value string) error { opened = value; return nil }
	for index, action := range m.serviceActions() {
		if action.id == "service-dashboard" {
			m.serviceAction = index
			break
		}
	}
	_, command := m.activateServiceAction()
	if command == nil {
		t.Fatal("Mailpit dashboard action is missing")
	}
	command()
	if opened != url {
		t.Fatalf("opened %q, want %q", opened, url)
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

func executeCommand(command tea.Cmd) []tea.Msg {
	if command == nil {
		return nil
	}
	message := command()
	if batch, ok := message.(tea.BatchMsg); ok {
		messages := []tea.Msg{}
		for _, child := range batch {
			messages = append(messages, executeCommand(child)...)
		}
		return messages
	}
	return []tea.Msg{message}
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

func TestEnterOpensSiteDetailAndEscapeReturns(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.tab, m.snapshot = 1, sampleSnapshot()
	updated, _ := m.handleKey(press(tea.KeyEnter, "", 0))
	m = updated.(Model)
	if !m.detailOpen || m.detailSite != "linguine" {
		t.Fatal("enter did not open the selected site's detail view")
	}
	if got := ansi.Strip(m.renderSiteDetail()); !strings.HasPrefix(got, "linguine.test  /srv/linguine\n\n") {
		t.Fatalf("unexpected site detail view: %q", got)
	}
	updated, _ = m.handleKey(press(tea.KeyEscape, "", 0))
	m = updated.(Model)
	if m.detailOpen {
		t.Fatal("escape did not return to the sites table")
	}
}

func TestSiteDetailMirrorsDesktopInformationAndActions(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.snapshot, m.detailOpen, m.detailSite, m.width = sampleSnapshot(), true, "linguine", 120
	rendered := ansi.Strip(m.renderSiteDetail())
	for _, expected := range []string{
		"Details", "Security", "HTTPS", "PHP version", "8.5", "Node.js version", "22",
		"URL", "https://linguine.test", "Path", "/srv/linguine", "Terminal", "Zed",
		"Workers", "Queue", "● Active · Stop", "Queue autostart", "Queue logs",
		"Reverb", "○ Inactive · Start", "Reverb autostart", "Reverb logs",
	} {
		if !strings.Contains(rendered, expected) {
			t.Fatalf("site detail missing %q: %q", expected, rendered)
		}
	}
}

func TestDetailLinkUnderlineDoesNotCoverCellPadding(t *testing.T) {
	styled := styledDetailValue("Open", 12, newStyles(true, nil).accent.Underline(true))
	if !strings.HasSuffix(styled, "        ") {
		t.Fatalf("detail link padding is still inside its ANSI style: %q", styled)
	}
	if ansi.StringWidth(styled) != 12 {
		t.Fatalf("detail link cell width = %d, want 12", ansi.StringWidth(styled))
	}
}

func TestSiteDetailFitsANormalTerminal(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.loaded, m.snapshot, m.tab = true, sampleSnapshot(), 1
	m.detailOpen, m.detailSite, m.width, m.height = true, "linguine", 80, 24
	if lines := strings.Count(m.render(), "\n") + 1; lines > m.height {
		t.Fatalf("site detail uses %d lines in a %d-line terminal", lines, m.height)
	}
}

func TestSiteDetailActionsUseControllerAndDesktopLaunchCommands(t *testing.T) {
	api := &fakeAPI{}
	m := NewWithAPI(api)
	m.snapshot, m.detailOpen, m.detailSite = sampleSnapshot(), true, "linguine"
	m.snapshot.Sites.PHPVersions = []string{"8.4", "8.5"}

	// Addressed by action id, not by position: the row order changes whenever
	// the detail view gains a field, and an index-based test then exercises
	// whatever moved into that slot instead of failing.
	at := func(id string) {
		t.Helper()
		for index, action := range m.detailActions() {
			if action.id == id {
				m.busy, m.detailCursor = false, index
				return
			}
		}
		t.Fatalf("no detail action %q", id)
	}
	last := func() string {
		t.Helper()
		if len(api.calls) == 0 {
			t.Fatal("no controller call was made")
		}
		return api.calls[len(api.calls)-1]
	}

	at("security")
	_, command := m.activateDetailAction()
	executeCommand(command)
	if last() != "secured:linguine:false" {
		t.Fatalf("security action calls = %v", api.calls)
	}

	at("php")
	_, command = m.changeDetailVersion(-1)
	executeCommand(command)
	if last() != "php:linguine:8.4" {
		t.Fatalf("PHP action calls = %v", api.calls)
	}

	at("queue-active")
	_, command = m.activateDetailAction()
	executeCommand(command)
	if last() != "active:linguine:queue:false" {
		t.Fatalf("queue action calls = %v", api.calls)
	}

	launched := ""
	m.launchCommand = func(name string, arguments ...string) error {
		launched = name + " " + strings.Join(arguments, " ")
		return nil
	}
	at("terminal")
	_, command = m.activateDetailAction()
	command()
	if launched != "xdg-terminal-exec --dir=/srv/linguine" {
		t.Fatalf("terminal action launched %q", launched)
	}

	// Editing prepares the fragment through the controller before handing it
	// to the desktop, so the editor never opens on a file that is not there.
	at("nginx-edit")
	_, command = m.activateDetailAction()
	command()
	if last() != "ensure:linguine" {
		t.Fatalf("nginx edit calls = %v", api.calls)
	}
	if launched != "omarchy-launch-editor /home/demo/.config/paddock/nginx/linguine.custom.conf" {
		t.Fatalf("nginx edit launched %q", launched)
	}

	// A launcher failure must be visible instead of looking like Enter did
	// nothing, which was especially confusing for terminal-based editors.
	m.launchCommand = func(string, ...string) error { return errors.New("editor unavailable") }
	_, command = m.activateDetailAction()
	message := command().(mutationMsg)
	if message.err == nil || !strings.Contains(message.err.Error(), "editor unavailable") {
		t.Fatalf("nginx editor error = %v", message.err)
	}

	// The fixture's project fragment is pending, so activating trusts it.
	at("project-config")
	_, command = m.activateDetailAction()
	command()
	if last() != "trusted:linguine:true" {
		t.Fatalf("project config calls = %v", api.calls)
	}

	at("nginx-reload")
	_, command = m.activateDetailAction()
	command()
	if last() != "reload-web" {
		t.Fatalf("reload calls = %v", api.calls)
	}
}

func TestProjectConfigurationRowOnlyAppearsWhenOneIsDeclared(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.snapshot, m.detailOpen, m.detailSite = sampleSnapshot(), true, "linguine"
	has := func() bool {
		for _, action := range m.detailActions() {
			if action.id == "project-config" {
				return true
			}
		}
		return false
	}
	if !has() {
		t.Fatal("a declared project fragment should be offered for review")
	}
	m.snapshot.Sites.Sites[0].ProjectConfig = nil
	m.snapshot.Sites.Sites[0].ProjectConfigStatus = "none"
	if has() {
		t.Fatal("a site with no project fragment should not offer the row")
	}
}

func TestBrokenNginxConfigurationIsShownOnTheEditRow(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.snapshot, m.detailOpen, m.detailSite = sampleSnapshot(), true, "linguine"
	m.snapshot.Sites.Sites[0].NginxConfigError = true
	if label := nginxConfigLabel(m.snapshot.Sites.Sites[0]); !strings.Contains(label, "Error") {
		t.Fatalf("nginx config label = %q", label)
	}
	if view := ansi.Strip(m.renderSiteDetail()); !strings.Contains(view, "Error · Edit") {
		t.Fatalf("site details did not show nginx error:\n%s", view)
	}
}

func TestOpenUsesTheSelectedSitesURL(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.tab, m.snapshot = 1, sampleSnapshot()
	opened := ""
	m.openURL = func(url string) error {
		opened = url
		return nil
	}
	_, command := m.handleKey(press('o', "o", 0))
	if command == nil {
		t.Fatal("o did not create a browser-open command")
	}
	message := command()
	if opened != "https://linguine.test" {
		t.Fatalf("opened %q instead of the selected site's URL", opened)
	}
	if result, ok := message.(openSiteMsg); !ok || result.err != nil {
		t.Fatalf("unexpected browser-open result: %#v", message)
	}
}

func TestMouseClickOpensRowOrItsOpenLink(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.tab, m.snapshot, m.width, m.height = 1, sampleSnapshot(), 80, 24
	updated, _ := m.handleMouseClick(tea.MouseClickMsg{
		X: 4, Y: 8, Button: tea.MouseLeft,
	})
	m = updated.(Model)
	if !m.detailOpen || m.detailSite != "linguine" {
		t.Fatal("clicking a site row did not open its detail view")
	}

	m.detailOpen = false
	opened := ""
	m.openURL = func(url string) error { opened = url; return nil }
	nameWidth := max(7, max(40, m.width-8)-33)
	updated, command := m.handleMouseClick(tea.MouseClickMsg{
		X: 31 + nameWidth, Y: 8, Button: tea.MouseLeft,
	})
	_ = updated
	if command == nil {
		t.Fatal("clicking Open did not create a browser command")
	}
	command()
	if opened != "https://linguine.test" {
		t.Fatalf("Open link launched %q", opened)
	}
}

func TestSitesAlwaysShowASearchBox(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.loaded, m.snapshot, m.tab, m.width = true, sampleSnapshot(), 1, 80
	rendered := ansi.Strip(m.renderSites())
	if !strings.Contains(rendered, "┌─ Search ") || !strings.Contains(rendered, "Type / to search sites") {
		t.Fatalf("persistent search box is missing: %q", rendered)
	}
	updated, _ := m.handleMouseClick(tea.MouseClickMsg{X: 5, Y: 4, Button: tea.MouseLeft})
	if !updated.(Model).filtering {
		t.Fatal("clicking the search box did not focus it")
	}
}

func TestToastHasItsOwnRowAndExpiresWithoutClearingANewerToast(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.tab, m.status, m.toastID = 1, "Stopped queue worker", 4
	footer := ansi.Strip(m.renderFooter())
	lines := strings.Split(footer, "\n")
	if len(lines) < 3 || !strings.Contains(lines[1], "enter details") {
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

func TestFailedDashboardMutationSurvivesRefreshAndExpires(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.loaded, m.snapshot, m.generation = true, sampleSnapshot(), 3
	detail := "nginx: unknown directive"
	updated, command := m.Update(dashboardMutationMsg{3, backend.DashboardOperationResult{
		OK: false, Summary: "The web configuration was rejected", Detail: &detail,
		Snapshot: m.snapshot.Dashboard,
	}, nil})
	m = updated.(Model)
	if !strings.Contains(m.err, "unknown directive") || command == nil {
		t.Fatalf("reload failure was not retained: err=%q command=%v", m.err, command)
	}

	updated, _ = m.Update(snapshotMsg{generation: m.generation, snapshot: sampleSnapshot()})
	m = updated.(Model)
	if !strings.Contains(m.err, "unknown directive") {
		t.Fatal("the refresh immediately cleared the reload error")
	}
	updated, _ = m.Update(errorExpiredMsg(m.errorID))
	if updated.(Model).err != "" {
		t.Fatal("the reload error did not expire")
	}
}

func TestErrorDetailsScrollCopyAndPauseExpiry(t *testing.T) {
	m := NewWithAPI(&fakeAPI{})
	m.loaded, m.snapshot, m.width, m.height = true, sampleSnapshot(), 48, 14
	m.err, m.errorID = strings.Repeat("directive failure ", 12), 7
	updated, _ := m.handleKey(press(tea.KeyEnter, "enter", 0))
	m = updated.(Model)
	if !m.errorOpen || !strings.Contains(ansi.Strip(m.render()), "Error details") {
		t.Fatal("enter did not open the complete error view")
	}
	updated, _ = m.handleKey(press(tea.KeyDown, "down", 0))
	m = updated.(Model)
	if m.errorOffset != 1 {
		t.Fatal("error details did not scroll")
	}

	copied := ""
	m.copyText = func(value string) error { copied = value; return nil }
	_, command := m.handleKey(press('c', "c", 0))
	if command == nil {
		t.Fatal("copy did not create a command")
	}
	message := command()
	if copied != m.err {
		t.Fatalf("copied truncated error %q", copied)
	}
	updated, _ = m.Update(message)
	m = updated.(Model)
	if m.status != "Copied error" || !m.errorOpen {
		t.Fatal("copy feedback closed the error view")
	}

	updated, command = m.Update(errorExpiredMsg(7))
	m = updated.(Model)
	if m.err == "" || command == nil {
		t.Fatal("automatic expiry was not paused while details were open")
	}
	updated, _ = m.handleKey(press(tea.KeyEscape, "esc", 0))
	m = updated.(Model)
	if m.errorOpen || m.err != "" {
		t.Fatal("escape did not close and dismiss the error")
	}
}

func TestOpenLogsCanBeCopiedAndRefreshed(t *testing.T) {
	api := &fakeAPI{}
	m := NewWithAPI(api)
	m.logsOpen, m.logs = true, []string{"first", "second"}
	m.logKind, m.logServiceID, m.logTitle = "service", "redis-a1", "Cache logs"
	copied := ""
	m.copyText = func(value string) error { copied = value; return nil }

	_, command := m.handleKey(press('c', "c", 0))
	if command == nil {
		t.Fatal("log copy did not create a command")
	}
	updated, _ := m.Update(command())
	m = updated.(Model)
	if copied != "first\nsecond\n" || m.status != "Copied logs" || !m.logsOpen {
		t.Fatalf("log copy result: copied=%q status=%q open=%t", copied, m.status, m.logsOpen)
	}

	updated, command = m.handleKey(press('r', "r", 0))
	m = updated.(Model)
	if !m.busy || command == nil {
		t.Fatal("log refresh did not start")
	}
	executeCommand(command)
	if api.calls[len(api.calls)-1] != "service-logs:redis-a1:200" {
		t.Fatalf("service log refresh calls = %v", api.calls)
	}

	m.busy, m.logKind, m.logSite, m.logWorker = false, "worker", "linguine", "queue"
	_, command = m.refreshLogs()
	executeCommand(command)
	if api.calls[len(api.calls)-1] != "logs:linguine:queue:200" {
		t.Fatalf("worker log refresh calls = %v", api.calls)
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
