package tui

import (
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/charmbracelet/x/ansi"

	"github.com/lukeska/paddock/internal/backend"
)

const (
	refreshInterval = 5 * time.Second
	toastDuration   = 3 * time.Second
)

type API interface {
	Snapshot() (backend.Snapshot, error)
	SetDashboardActive(active bool) (backend.DashboardOperationResult, error)
	CreateService(kind, label string, port *int, autostart bool) (backend.ServiceOperationResult, error)
	SetServiceActive(id string, active bool) (backend.ServiceOperationResult, error)
	UpdateService(id, label string, port int, autostart bool) (backend.ServiceOperationResult, error)
	RemoveService(id string) (backend.ServiceOperationResult, error)
	ServiceLogs(id string, lines int) (backend.ServiceLogsResult, error)
	SetActive(site, worker string, active bool) (backend.OperationResult, error)
	SetAutostart(site, worker string, enabled bool) (backend.OperationResult, error)
	SetSitePHP(site, version string) (backend.OperationResult, error)
	SetSiteNode(site, version string) (backend.OperationResult, error)
	SetSiteSecured(site string, secured bool) (backend.OperationResult, error)
	SetSiteConfigurationTrusted(site string, trusted bool) (backend.OperationResult, error)
	EnsureSiteConfiguration(site string) (backend.OperationResult, error)
	ReloadWeb() (backend.DashboardOperationResult, error)
	Logs(site, worker string, lines int) (backend.LogsResult, error)
	Close() error
}

type Model struct {
	api            API
	python         string
	snapshot       backend.Snapshot
	loaded         bool
	width          int
	height         int
	tab            int
	cursor         int
	worker         int
	detailOpen     bool
	detailSite     string
	detailCursor   int
	serviceCursor  int
	serviceDetail  bool
	serviceID      string
	serviceAction  int
	serviceForm    string
	serviceField   int
	serviceType    int
	serviceLabel   string
	servicePort    string
	serviceBoot    bool
	serviceConfirm bool
	serviceBusy    bool
	openURL        func(string) error
	launchCommand  func(string, ...string) error
	filtering      bool
	filter         string
	logs           []string
	logsOpen       bool
	logOffset      int
	busy           bool
	dashboardBusy  bool
	dashboardGoal  bool
	spinnerFrame   int
	status         string
	toastID        int
	logTitle       string
	err            string
	generation     int
	terminalDark   bool
	styles         styles
}

type connectedMsg struct {
	api API
	err error
}
type snapshotMsg struct {
	generation int
	snapshot   backend.Snapshot
	err        error
}
type mutationMsg struct {
	generation int
	result     backend.OperationResult
	err        error
}
type dashboardMutationMsg struct {
	generation int
	result     backend.DashboardOperationResult
	err        error
}
type serviceMutationMsg struct {
	generation int
	result     backend.ServiceOperationResult
	err        error
}
type serviceLogsMsg struct {
	label  string
	result backend.ServiceLogsResult
	err    error
}
type logsMsg struct {
	site, worker string
	lines        []string
	err          error
}
type tickMsg time.Time
type toastExpiredMsg int
type spinnerTickMsg int
type openSiteMsg struct {
	host string
	err  error
}
type externalActionMsg struct {
	summary string
	err     error
}

type detailAction struct {
	id, section, label, value string
}

func New(python string) Model {
	return Model{
		python: python, width: 80, height: 24, generation: 1,
		terminalDark: true, styles: newStyles(true, nil),
		openURL: func(url string) error {
			return exec.Command("xdg-open", url).Run()
		},
		launchCommand: func(name string, arguments ...string) error {
			return exec.Command(name, arguments...).Run()
		},
	}
}

func NewWithAPI(api API) Model {
	m := New("")
	m.api = api
	return m
}

func (m Model) API() API { return m.api }

func (m Model) Init() tea.Cmd {
	commands := []tea.Cmd{tick(), func() tea.Msg { return tea.RequestBackgroundColor() }}
	if m.api == nil {
		commands = append(commands, connect(m.python))
	} else {
		commands = append(commands, load(m.api, m.generation))
	}
	return tea.Batch(commands...)
}

func connect(python string) tea.Cmd {
	return func() tea.Msg {
		client, err := backend.Start(python)
		if err != nil {
			return connectedMsg{err: err}
		}
		return connectedMsg{api: client}
	}
}

func load(api API, generation int) tea.Cmd {
	return func() tea.Msg {
		snapshot, err := api.Snapshot()
		return snapshotMsg{generation, snapshot, err}
	}
}

func tick() tea.Cmd {
	return tea.Tick(refreshInterval, func(now time.Time) tea.Msg { return tickMsg(now) })
}

func (m Model) Update(message tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := message.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = msg.Width, msg.Height
	case tea.BackgroundColorMsg:
		m.terminalDark = msg.IsDark()
		if !m.loaded || m.snapshot.Theme == nil {
			m.styles = newStyles(m.terminalDark, nil)
		}
	case connectedMsg:
		if msg.err != nil {
			m.err = "Paddock backend is unavailable: " + msg.err.Error()
			m.busy = false
			return m, nil
		}
		m.api, m.err, m.busy = msg.api, "", true
		m.generation++
		return m, load(m.api, m.generation)
	case snapshotMsg:
		if msg.generation != m.generation {
			return m, nil
		}
		m.busy = false
		if msg.err != nil {
			m.err = "Could not refresh Paddock: " + msg.err.Error()
			return m, nil
		}
		m.snapshot, m.loaded, m.err = msg.snapshot, true, ""
		m.styles = newStyles(m.terminalDark, msg.snapshot.Theme)
		m.clampCursor()
		m.clampDetailCursor()
		m.clampServiceCursor()
	case mutationMsg:
		if msg.generation != m.generation {
			return m, nil
		}
		m.busy = false
		if msg.err != nil {
			m.err = msg.err.Error()
			return m, nil
		}
		m.snapshot.Sites = msg.result.Snapshot
		m.status = ""
		var toast tea.Cmd
		if !msg.result.OK {
			m.err = msg.result.Summary
			if msg.result.Detail != nil {
				m.err += ": " + *msg.result.Detail
			}
		} else {
			m.err = ""
			m.status = msg.result.Summary
			m.toastID++
			toast = dismissToast(m.toastID)
		}
		m.generation++
		m.busy = true
		return m, tea.Batch(load(m.api, m.generation), toast)
	case dashboardMutationMsg:
		if msg.generation != m.generation {
			return m, nil
		}
		m.busy, m.dashboardBusy = false, false
		if msg.err != nil {
			m.err = msg.err.Error()
			return m, nil
		}
		m.snapshot.Dashboard = msg.result.Snapshot
		m.status = ""
		var toast tea.Cmd
		if !msg.result.OK {
			m.err = msg.result.Summary
			if msg.result.Detail != nil {
				m.err += ": " + *msg.result.Detail
			}
		} else {
			m.err = ""
			m.status = msg.result.Summary
			m.toastID++
			toast = dismissToast(m.toastID)
		}
		m.generation++
		m.busy = true
		return m, tea.Batch(load(m.api, m.generation), toast)
	case serviceMutationMsg:
		if msg.generation != m.generation {
			return m, nil
		}
		m.busy, m.serviceBusy = false, false
		if msg.err != nil {
			m.err = msg.err.Error()
			return m, nil
		}
		m.snapshot.Services = msg.result.Snapshot
		if !msg.result.OK {
			m.err = msg.result.Summary
			if msg.result.Detail != nil {
				m.err += ": " + *msg.result.Detail
			}
			return m, nil
		}
		m.err, m.status, m.serviceForm, m.serviceConfirm = "", msg.result.Summary, "", false
		m.toastID++
		m.clampServiceCursor()
		if m.serviceDetail {
			if _, ok := m.selectedServiceDetail(); !ok {
				m.serviceDetail = false
			}
		}
		m.generation++
		m.busy = true
		return m, tea.Batch(load(m.api, m.generation), dismissToast(m.toastID))
	case serviceLogsMsg:
		m.busy = false
		if msg.err != nil {
			m.err = msg.err.Error()
			return m, nil
		}
		if !msg.result.OK {
			m.err = msg.result.Summary
			if msg.result.Detail != nil {
				m.err += ": " + *msg.result.Detail
			}
			return m, nil
		}
		m.logs, m.logsOpen, m.logOffset, m.err = msg.result.Lines, true, 0, ""
		m.logTitle = msg.label + " logs"
	case spinnerTickMsg:
		if (m.dashboardBusy || m.serviceBusy) && int(msg) == m.generation {
			m.spinnerFrame++
			return m, spinnerTick(m.generation)
		}
	case openSiteMsg:
		if msg.err != nil {
			m.err = "Could not open " + msg.host + ": " + msg.err.Error()
			return m, nil
		}
		m.err = ""
		m.status = "Opened " + msg.host
		m.toastID++
		return m, dismissToast(m.toastID)
	case externalActionMsg:
		if msg.err != nil {
			m.err = msg.summary + ": " + msg.err.Error()
			return m, nil
		}
		m.err, m.status = "", msg.summary
		m.toastID++
		return m, dismissToast(m.toastID)
	case logsMsg:
		m.busy = false
		if msg.err != nil {
			m.err = msg.err.Error()
			return m, nil
		}
		m.logs, m.logsOpen, m.logOffset, m.err = msg.lines, true, 0, ""
		m.logTitle = fmt.Sprintf("%s logs for %s.test", title(msg.worker), msg.site)
	case toastExpiredMsg:
		if int(msg) == m.toastID {
			m.status = ""
		}
	case tickMsg:
		command := tick()
		if m.api != nil && !m.busy {
			m.generation++
			m.busy = true
			return m, tea.Batch(command, load(m.api, m.generation))
		}
		return m, command
	case tea.MouseClickMsg:
		return m.handleMouseClick(msg)
	case tea.KeyPressMsg:
		return m.handleKey(msg)
	}
	return m, nil
}

func (m Model) handleMouseClick(msg tea.MouseClickMsg) (tea.Model, tea.Cmd) {
	if msg.Button != tea.MouseLeft || m.tab != 1 || m.detailOpen {
		return m, nil
	}
	if msg.Y >= 3 && msg.Y <= 5 {
		m.filtering = true
		return m, nil
	}
	if m.filtering {
		return m, nil
	}
	sites := m.filteredSites()
	start, _ := m.siteWindow(len(sites))
	firstRow := 8
	visibleRow := msg.Y - firstRow
	if visibleRow < 0 || start+visibleRow >= len(sites) {
		return m, nil
	}
	m.cursor = start + visibleRow
	nameWidth := max(7, max(40, m.width-8)-33)
	openColumn := 31 + nameWidth
	if msg.X >= openColumn && msg.X < openColumn+4 {
		return m.openSelectedSite()
	}
	m.detailOpen, m.detailSite = true, sites[m.cursor].Name
	return m, nil
}

func (m Model) handleKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	key := msg.String()
	if key == "ctrl+c" || (!m.filtering && m.serviceForm == "" && !m.serviceConfirm && key == "q") {
		return m, tea.Quit
	}
	if m.logsOpen {
		switch key {
		case "esc", "q", "l":
			m.logsOpen = false
		case "j", "down":
			if m.logOffset < max(0, len(m.logs)-m.logHeight()) {
				m.logOffset++
			}
		case "k", "up":
			if m.logOffset > 0 {
				m.logOffset--
			}
		}
		return m, nil
	}
	if m.filtering {
		switch key {
		case "esc":
			m.filtering, m.filter = false, ""
		case "enter":
			m.filtering = false
		case "backspace":
			if m.filter != "" {
				_, size := utf8.DecodeLastRuneInString(m.filter)
				m.filter = m.filter[:len(m.filter)-size]
			}
		default:
			if utf8.RuneCountInString(key) == 1 {
				m.filter += key
			}
		}
		m.cursor = 0
		m.clampCursor()
		return m, nil
	}
	if m.serviceConfirm {
		switch key {
		case "y", "Y", "enter":
			return m.removeSelectedService()
		case "n", "N", "esc":
			m.serviceConfirm = false
		}
		return m, nil
	}
	if m.serviceForm != "" {
		return m.handleServiceFormKey(key)
	}
	if m.err != "" && (key == "esc" || key == "enter") {
		m.err = ""
		return m, nil
	}
	if m.detailOpen {
		switch key {
		case "esc", "backspace":
			m.detailOpen = false
		case "j", "down":
			if m.detailCursor < len(m.detailActions())-1 {
				m.detailCursor++
			}
		case "k", "up":
			if m.detailCursor > 0 {
				m.detailCursor--
			}
		case "h", "left":
			return m.changeDetailVersion(-1)
		case "l", "right":
			return m.changeDetailVersion(1)
		case "enter", " ", "space":
			return m.activateDetailAction()
		case "o":
			return m.openSelectedSite()
		}
		return m, nil
	}
	if m.serviceDetail {
		switch key {
		case "esc", "backspace":
			m.serviceDetail = false
		case "j", "down":
			if m.serviceAction < len(m.serviceActions())-1 {
				m.serviceAction++
			}
		case "k", "up":
			if m.serviceAction > 0 {
				m.serviceAction--
			}
		case "enter", " ", "space":
			return m.activateServiceAction()
		}
		return m, nil
	}
	if key == "r" {
		m.status = ""
		m.toastID++
		if m.api == nil {
			m.busy = true
			return m, connect(m.python)
		}
		if !m.busy {
			m.generation++
			m.busy = true
			return m, load(m.api, m.generation)
		}
		return m, nil
	}
	switch key {
	case "tab":
		m.tab = (m.tab + 1) % 3
		m.cursor = 0
		m.detailOpen = false
	case "shift+tab":
		m.tab = (m.tab + 3 - 1) % 3
		m.cursor = 0
		m.detailOpen = false
	case "1":
		m.tab = 0
		m.cursor = 0
		m.detailOpen = false
	case "2":
		m.tab = 1
		m.cursor = 0
		m.detailOpen = false
	case "3":
		m.tab = 2
		m.serviceCursor = 0
		m.serviceDetail = false
	case "j", "down":
		if m.tab == 1 && m.cursor < len(m.filteredSites())-1 {
			m.cursor++
		} else if m.tab == 2 && m.serviceCursor < len(m.snapshot.Services.Instances)-1 {
			m.serviceCursor++
		}
	case "k", "up":
		if m.tab == 1 && m.cursor > 0 {
			m.cursor--
		} else if m.tab == 2 && m.serviceCursor > 0 {
			m.serviceCursor--
		}
	case "/":
		if m.tab == 1 {
			m.filtering = true
		}
	case " ", "space":
		if m.tab == 0 {
			return m.mutateDashboard()
		}
	case "enter":
		if m.tab == 1 {
			if site, ok := m.selected(); ok {
				m.detailOpen, m.detailSite, m.detailCursor = true, site.Name, 0
			}
		} else if m.tab == 2 {
			if service, ok := m.selectedService(); ok {
				m.serviceDetail, m.serviceID, m.serviceAction = true, service.ID, 0
			}
		}
	case "a":
		if m.tab == 2 {
			m.beginAddService()
		}
	case "g":
		if m.tab == 1 {
			m.cursor = 0
		}
	case "G":
		if m.tab == 1 {
			m.cursor = max(0, len(m.filteredSites())-1)
		}
	case "o":
		return m.openSelectedSite()
	}
	return m, nil
}

func (m Model) openSelectedSite() (tea.Model, tea.Cmd) {
	if m.tab != 1 || m.openURL == nil {
		return m, nil
	}
	site, ok := m.selected()
	if m.detailOpen {
		for _, candidate := range m.snapshot.Sites.Sites {
			if candidate.Name == m.detailSite {
				site, ok = candidate, true
				break
			}
		}
	}
	if !ok {
		return m, nil
	}
	opener, url, host := m.openURL, site.URL, site.Host
	return m, func() tea.Msg {
		return openSiteMsg{host: host, err: opener(url)}
	}
}

func (m Model) dashboardAllActive() bool {
	found := false
	for _, service := range m.snapshot.Dashboard.Services {
		if !service.Configured {
			continue
		}
		found = true
		if service.State != "active" {
			return false
		}
	}
	return found
}

func (m Model) mutateDashboard() (tea.Model, tea.Cmd) {
	if m.tab != 0 || m.busy || m.api == nil {
		return m, nil
	}
	active := !m.dashboardAllActive()
	m.busy, m.dashboardBusy, m.dashboardGoal = true, true, active
	m.spinnerFrame, m.status, m.err = 0, "", ""
	m.toastID++
	m.generation++
	generation, api := m.generation, m.api
	operation := func() tea.Msg {
		result, err := api.SetDashboardActive(active)
		return dashboardMutationMsg{generation, result, err}
	}
	return m, tea.Batch(operation, spinnerTick(generation))
}

func (m Model) activateServiceAction() (tea.Model, tea.Cmd) {
	actions := m.serviceActions()
	if m.serviceAction < 0 || m.serviceAction >= len(actions) || m.busy {
		return m, nil
	}
	service, ok := m.selectedServiceDetail()
	if !ok {
		return m, nil
	}
	switch actions[m.serviceAction].id {
	case "service-active":
		return m.mutateServiceActive(service)
	case "service-autostart":
		m.beginEditService(service)
		m.serviceField = 3
		m.serviceBoot = !m.serviceBoot
		return m.saveServiceForm()
	case "service-logs":
		return m.openServiceLogs(service)
	case "service-dashboard":
		return m.openServiceDashboard(service)
	case "service-edit":
		m.beginEditService(service)
	case "service-remove":
		m.serviceConfirm = true
	}
	return m, nil
}

func (m Model) openServiceDashboard(service backend.ServiceInstance) (tea.Model, tea.Cmd) {
	if m.openURL == nil || service.DashboardURL == nil {
		return m, nil
	}
	opener, url, label := m.openURL, *service.DashboardURL, service.Label
	return m, func() tea.Msg {
		return externalActionMsg{summary: "Opened " + label + " dashboard", err: opener(url)}
	}
}

func (m Model) mutateServiceActive(service backend.ServiceInstance) (tea.Model, tea.Cmd) {
	if m.api == nil || m.busy {
		return m, nil
	}
	m.busy, m.serviceBusy, m.err, m.status = true, true, "", ""
	m.generation++
	generation, api := m.generation, m.api
	return m, tea.Batch(func() tea.Msg {
		result, err := api.SetServiceActive(service.ID, service.State != "active")
		return serviceMutationMsg{generation, result, err}
	}, spinnerTick(generation))
}

func (m Model) openServiceLogs(service backend.ServiceInstance) (tea.Model, tea.Cmd) {
	if m.api == nil || m.busy {
		return m, nil
	}
	m.busy = true
	api := m.api
	return m, func() tea.Msg {
		result, err := api.ServiceLogs(service.ID, 200)
		return serviceLogsMsg{service.Label, result, err}
	}
}

func (m Model) removeSelectedService() (tea.Model, tea.Cmd) {
	service, ok := m.selectedServiceDetail()
	if !ok || m.api == nil || m.busy {
		return m, nil
	}
	m.serviceConfirm, m.busy, m.serviceBusy, m.err, m.status = false, true, true, "", ""
	m.generation++
	generation, api := m.generation, m.api
	return m, tea.Batch(func() tea.Msg {
		result, err := api.RemoveService(service.ID)
		return serviceMutationMsg{generation, result, err}
	}, spinnerTick(generation))
}

func (m Model) handleServiceFormKey(key string) (tea.Model, tea.Cmd) {
	switch key {
	case "esc":
		m.serviceForm = ""
		return m, nil
	case "tab", "down":
		m.serviceField = (m.serviceField + 1) % 4
		return m, nil
	case "shift+tab", "up":
		m.serviceField = (m.serviceField + 3) % 4
		return m, nil
	case "left":
		if m.serviceField == 0 && m.serviceForm == "add" {
			m.cycleServiceType(-1)
		}
		if m.serviceField == 3 {
			m.serviceBoot = !m.serviceBoot
		}
		return m, nil
	case "right", " ", "space":
		if m.serviceField == 0 && m.serviceForm == "add" {
			m.cycleServiceType(1)
			return m, nil
		}
		if m.serviceField == 3 {
			m.serviceBoot = !m.serviceBoot
			return m, nil
		}
	case "enter":
		return m.saveServiceForm()
	case "backspace":
		if m.serviceField == 1 {
			m.serviceLabel = trimLastRune(m.serviceLabel)
		}
		if m.serviceField == 2 {
			m.servicePort = trimLastRune(m.servicePort)
		}
		return m, nil
	}
	if utf8.RuneCountInString(key) == 1 {
		if m.serviceField == 1 {
			m.serviceLabel += key
		}
		if m.serviceField == 2 && key[0] >= '0' && key[0] <= '9' {
			m.servicePort += key
		}
	}
	return m, nil
}

func trimLastRune(value string) string {
	if value == "" {
		return value
	}
	_, size := utf8.DecodeLastRuneInString(value)
	return value[:len(value)-size]
}

func (m *Model) cycleServiceType(direction int) {
	m.serviceType = (m.serviceType + direction + len(serviceKinds)) % len(serviceKinds)
	m.serviceLabel, m.servicePort = serviceKinds[m.serviceType].label, serviceKinds[m.serviceType].port
}

func (m Model) saveServiceForm() (tea.Model, tea.Cmd) {
	if m.api == nil || m.busy {
		return m, nil
	}
	label := strings.TrimSpace(m.serviceLabel)
	if label == "" {
		m.err = "Service name cannot be empty"
		return m, nil
	}
	port, err := strconv.Atoi(m.servicePort)
	if err != nil || port < 1024 || port > 65535 {
		m.err = "Port must be between 1024 and 65535"
		return m, nil
	}
	m.busy, m.serviceBusy, m.err, m.status = true, true, "", ""
	m.generation++
	generation, api, form := m.generation, m.api, m.serviceForm
	serviceID, kind, boot := m.serviceID, serviceKinds[m.serviceType].kind, m.serviceBoot
	return m, tea.Batch(func() tea.Msg {
		var result backend.ServiceOperationResult
		var callErr error
		if form == "add" {
			result, callErr = api.CreateService(kind, label, &port, boot)
		} else {
			result, callErr = api.UpdateService(serviceID, label, port, boot)
		}
		return serviceMutationMsg{generation, result, callErr}
	}, spinnerTick(generation))
}

func (m Model) selected() (backend.Site, bool) {
	sites := m.filteredSites()
	if m.cursor < 0 || m.cursor >= len(sites) {
		return backend.Site{}, false
	}
	return sites[m.cursor], true
}

func (m Model) detailSelectedSite() (backend.Site, bool) {
	for _, site := range m.snapshot.Sites.Sites {
		if site.Name == m.detailSite {
			return site, true
		}
	}
	return backend.Site{}, false
}

func (m Model) detailActions() []detailAction {
	site, ok := m.detailSelectedSite()
	if !ok {
		return nil
	}
	node := "Default"
	if site.Node != nil {
		node = *site.Node
	}
	security := " HTTP"
	if site.Secured {
		security = " HTTPS"
	}
	documentRoot := site.DocumentRoot
	if documentRoot == "." {
		documentRoot = "project root"
	}
	actions := []detailAction{
		{"security", "Details", "Security", security},
		{"php", "Details", "PHP version", site.PHP},
		{"node", "Details", "Node.js version", node},
		{"type", "Details", "Type", title(site.Type)},
		{"docroot", "Details", "Document root", documentRoot},
		{"url", "Details", "URL", site.URL},
		{"path", "Details", "Path", site.Root},
		{"terminal", "Details", "Terminal", "Open"},
		{"zed", "Details", "Zed", "Open"},
		{"nginx-edit", "Details", "Nginx config", nginxConfigLabel(site)},
	}
	// Only offered when the project actually ships a fragment, and the label
	// says what activating it will do, because trusting arbitrary server
	// configuration from a repository should never be a mystery keystroke.
	if site.ProjectConfig != nil && site.ProjectConfigStatus != "none" {
		actions = append(actions, detailAction{
			"project-config", "Details", "From project",
			projectConfigLabel(site.ProjectConfigStatus),
		})
	}
	actions = append(actions, detailAction{
		"nginx-reload", "Details", "Apply config edits", "Reload",
	})
	if site.QueueAvailable || site.QueueConfigured {
		actions = append(actions,
			detailAction{"queue-active", "Workers", "Queue", workerState(site.QueueConfigured, site.QueueState)},
			detailAction{"queue-autostart", "Workers", "Queue autostart", onOff(site.QueueAutostart)},
			detailAction{"queue-logs", "Workers", "Queue logs", "Open"},
		)
	}
	if site.ReverbAvailable || site.ReverbConfigured {
		actions = append(actions,
			detailAction{"reverb-active", "Workers", "Reverb", workerState(site.ReverbConfigured, site.ReverbState)},
			detailAction{"reverb-autostart", "Workers", "Reverb autostart", onOff(site.ReverbAutostart)},
			detailAction{"reverb-logs", "Workers", "Reverb logs", "Open"},
		)
	}
	return actions
}

func workerState(configured bool, state string) string {
	if !configured {
		return "Available · Start"
	}
	if state == "active" {
		return "Active · Stop"
	}
	return strings.ReplaceAll(title(state), "-", " ") + " · Start"
}

func onOff(value bool) string {
	if value {
		return "On"
	}
	return "Off"
}

func (m *Model) clampDetailCursor() {
	last := len(m.detailActions()) - 1
	if last < 0 {
		m.detailCursor = 0
	} else if m.detailCursor > last {
		m.detailCursor = last
	}
}

func (m Model) selectedDetailAction() (detailAction, bool) {
	actions := m.detailActions()
	if m.detailCursor < 0 || m.detailCursor >= len(actions) {
		return detailAction{}, false
	}
	return actions[m.detailCursor], true
}

func (m Model) activateDetailAction() (tea.Model, tea.Cmd) {
	action, ok := m.selectedDetailAction()
	if !ok || m.busy {
		return m, nil
	}
	switch action.id {
	case "security":
		return m.mutateSiteSecurity()
	case "php", "node":
		return m.changeDetailVersion(1)
	case "url":
		return m.openSelectedSite()
	case "path", "terminal", "zed":
		return m.launchDetailTool(action.id)
	case "nginx-edit":
		return m.editSiteConfiguration()
	case "project-config":
		return m.mutateProjectConfiguration()
	case "nginx-reload":
		return m.reloadWeb()
	case "type", "docroot":
		// Read-only: both follow from the project's own files, and changing
		// one is `paddock link --type`, not a keystroke in a dashboard.
		return m, nil
	case "queue-active":
		return m.mutateDetailWorker("queue", false)
	case "queue-autostart":
		return m.mutateDetailWorker("queue", true)
	case "queue-logs":
		return m.openDetailLogs("queue")
	case "reverb-active":
		return m.mutateDetailWorker("reverb", false)
	case "reverb-autostart":
		return m.mutateDetailWorker("reverb", true)
	case "reverb-logs":
		return m.openDetailLogs("reverb")
	}
	return m, nil
}

func (m Model) mutateSiteSecurity() (tea.Model, tea.Cmd) {
	if m.busy || m.api == nil {
		return m, nil
	}
	site, ok := m.detailSelectedSite()
	if !ok {
		return m, nil
	}
	m.busy, m.status, m.err = true, "", ""
	m.toastID++
	m.generation++
	generation, api := m.generation, m.api
	return m, func() tea.Msg {
		result, err := api.SetSiteSecured(site.Name, !site.Secured)
		return mutationMsg{generation, result, err}
	}
}

func (m Model) changeDetailVersion(direction int) (tea.Model, tea.Cmd) {
	action, ok := m.selectedDetailAction()
	if !ok || (action.id != "php" && action.id != "node") || m.busy || m.api == nil {
		return m, nil
	}
	site, ok := m.detailSelectedSite()
	if !ok {
		return m, nil
	}
	versions, current := m.snapshot.Sites.PHPVersions, site.PHP
	if action.id == "node" {
		versions = m.snapshot.Sites.NodeVersions
		current = ""
		if site.Node != nil {
			current = *site.Node
		}
	}
	if len(versions) == 0 {
		m.err = "No installed " + strings.ToUpper(action.id) + " versions are available"
		return m, nil
	}
	index := 0
	for candidate, version := range versions {
		if version == current {
			index = candidate
			break
		}
	}
	index = (index + direction + len(versions)) % len(versions)
	version := versions[index]
	if version == current {
		return m, nil
	}
	m.busy, m.status, m.err = true, "", ""
	m.toastID++
	m.generation++
	generation, api := m.generation, m.api
	return m, func() tea.Msg {
		var result backend.OperationResult
		var err error
		if action.id == "php" {
			result, err = api.SetSitePHP(site.Name, version)
		} else {
			result, err = api.SetSiteNode(site.Name, version)
		}
		return mutationMsg{generation, result, err}
	}
}

func (m Model) mutateDetailWorker(worker string, autostart bool) (tea.Model, tea.Cmd) {
	if m.busy || m.api == nil {
		return m, nil
	}
	site, ok := m.detailSelectedSite()
	if !ok {
		return m, nil
	}
	_, available, state, enabled := m.workerFor(site, worker)
	if !available {
		m.err = title(worker) + " is not available for this site"
		return m, nil
	}
	m.busy, m.status, m.err = true, "", ""
	m.toastID++
	m.generation++
	generation, api := m.generation, m.api
	return m, func() tea.Msg {
		var result backend.OperationResult
		var err error
		if autostart {
			result, err = api.SetAutostart(site.Name, worker, !enabled)
		} else {
			result, err = api.SetActive(site.Name, worker, state != "active")
		}
		return mutationMsg{generation, result, err}
	}
}

func (m Model) workerFor(site backend.Site, worker string) (string, bool, string, bool) {
	if worker == "queue" {
		return worker, site.QueueAvailable, site.QueueState, site.QueueAutostart
	}
	return worker, site.ReverbAvailable, site.ReverbState, site.ReverbAutostart
}

func (m Model) openDetailLogs(worker string) (tea.Model, tea.Cmd) {
	if m.busy || m.api == nil {
		return m, nil
	}
	site, ok := m.detailSelectedSite()
	if !ok {
		return m, nil
	}
	m.busy = true
	return m, func() tea.Msg {
		result, err := m.api.Logs(site.Name, worker, 200)
		return logsMsg{site.Name, worker, result.Lines, err}
	}
}

func (m Model) launchDetailTool(tool string) (tea.Model, tea.Cmd) {
	if m.launchCommand == nil {
		return m, nil
	}
	site, ok := m.detailSelectedSite()
	if !ok {
		return m, nil
	}
	launcher := m.launchCommand
	name, arguments, summary := "xdg-open", []string{site.Root}, "Opened project path"
	if tool == "terminal" {
		name, arguments, summary = "xdg-terminal-exec", []string{"--dir=" + site.Root}, "Opened terminal"
	} else if tool == "zed" {
		name, arguments, summary = "zeditor", []string{site.Root}, "Opened project in Zed"
	}
	return m, func() tea.Msg {
		return externalActionMsg{summary: summary, err: launcher(name, arguments...)}
	}
}

func nginxConfigLabel(site backend.Site) string {
	if site.CustomConfigPresent {
		return "Edit"
	}
	return "Create"
}

func projectConfigLabel(status string) string {
	switch status {
	case "trusted":
		return "Applied · Stop using"
	case "pending":
		return "Not reviewed · Trust"
	case "changed":
		return "Changed · Trust again"
	case "missing":
		return "Declared but missing"
	}
	return status
}

// editSiteConfiguration makes the fragment exist before handing it to the
// viewer's editor, because a UI cannot offer to edit a file that is not there
// and must not invent the template itself.
func (m Model) editSiteConfiguration() (tea.Model, tea.Cmd) {
	if m.busy || m.api == nil || m.launchCommand == nil {
		return m, nil
	}
	site, ok := m.detailSelectedSite()
	if !ok {
		return m, nil
	}
	m.busy, m.status, m.err = true, "", ""
	m.generation++
	generation, api, launcher := m.generation, m.api, m.launchCommand
	path := site.CustomConfig
	return m, func() tea.Msg {
		result, err := api.EnsureSiteConfiguration(site.Name)
		if err == nil && result.OK {
			// Ignored deliberately: the file is ready either way, and a
			// missing handler must not read as a failure to prepare it.
			_ = launcher("xdg-open", path)
		}
		return mutationMsg{generation, result, err}
	}
}

func (m Model) mutateProjectConfiguration() (tea.Model, tea.Cmd) {
	if m.busy || m.api == nil {
		return m, nil
	}
	site, ok := m.detailSelectedSite()
	if !ok {
		return m, nil
	}
	if site.ProjectConfigStatus == "missing" {
		m.err = "the project declares " + *site.ProjectConfig + ", but it is not there"
		return m, nil
	}
	trusted := site.ProjectConfigStatus != "trusted"
	m.busy, m.status, m.err = true, "", ""
	m.generation++
	generation, api := m.generation, m.api
	return m, func() tea.Msg {
		result, err := api.SetSiteConfigurationTrusted(site.Name, trusted)
		return mutationMsg{generation, result, err}
	}
}

func (m Model) reloadWeb() (tea.Model, tea.Cmd) {
	if m.busy || m.api == nil {
		return m, nil
	}
	m.busy, m.status, m.err = true, "", ""
	m.generation++
	generation, api := m.generation, m.api
	return m, func() tea.Msg {
		result, err := api.ReloadWeb()
		return dashboardMutationMsg{generation, result, err}
	}
}

func (m Model) selectedWorker(site backend.Site) (string, bool, string, bool) {
	if m.worker == 0 {
		return "queue", site.QueueAvailable, site.QueueState, site.QueueAutostart
	}
	return "reverb", site.ReverbAvailable, site.ReverbState, site.ReverbAutostart
}

func (m Model) mutateActive() (tea.Model, tea.Cmd) {
	if m.tab != 1 || m.busy || m.api == nil {
		return m, nil
	}
	site, ok := m.selected()
	if !ok {
		return m, nil
	}
	worker, available, state, _ := m.selectedWorker(site)
	if !available {
		m.err = title(worker) + " is not available for this site"
		return m, nil
	}
	m.busy, m.status, m.err = true, "", ""
	m.toastID++
	m.generation++
	generation, api := m.generation, m.api
	return m, func() tea.Msg {
		result, err := api.SetActive(site.Name, worker, state != "active")
		return mutationMsg{generation, result, err}
	}
}

func (m Model) mutateAutostart() (tea.Model, tea.Cmd) {
	if m.tab != 1 || m.busy || m.api == nil {
		return m, nil
	}
	site, ok := m.selected()
	if !ok {
		return m, nil
	}
	worker, available, _, autostart := m.selectedWorker(site)
	if !available {
		m.err = title(worker) + " is not available for this site"
		return m, nil
	}
	m.busy, m.status, m.err = true, "", ""
	m.toastID++
	m.generation++
	generation, api := m.generation, m.api
	return m, func() tea.Msg {
		result, err := api.SetAutostart(site.Name, worker, !autostart)
		return mutationMsg{generation, result, err}
	}
}

func (m Model) openLogs() (tea.Model, tea.Cmd) {
	if m.tab != 1 || m.busy || m.api == nil {
		return m, nil
	}
	site, ok := m.selected()
	if !ok {
		return m, nil
	}
	worker, available, _, _ := m.selectedWorker(site)
	if !available {
		m.err = title(worker) + " is not available for this site"
		return m, nil
	}
	m.busy = true
	return m, func() tea.Msg {
		result, err := m.api.Logs(site.Name, worker, 200)
		return logsMsg{site.Name, worker, result.Lines, err}
	}
}

func (m *Model) clampCursor() {
	last := len(m.filteredSites()) - 1
	if last < 0 {
		m.cursor = 0
	} else if m.cursor > last {
		m.cursor = last
	}
}

func (m Model) filteredSites() []backend.Site {
	if m.filter == "" {
		return m.snapshot.Sites.Sites
	}
	needle := strings.ToLower(m.filter)
	result := make([]backend.Site, 0)
	for _, site := range m.snapshot.Sites.Sites {
		if strings.Contains(strings.ToLower(site.Name), needle) || strings.Contains(strings.ToLower(site.Root), needle) {
			result = append(result, site)
		}
	}
	return result
}

func (m Model) View() tea.View {
	view := tea.NewView(m.render())
	view.AltScreen = true
	view.MouseMode = tea.MouseModeCellMotion
	view.WindowTitle = "Paddock"
	if m.snapshot.Theme != nil {
		view.BackgroundColor = paletteColor(m.snapshot.Theme.Background, nil)
		view.ForegroundColor = paletteColor(m.snapshot.Theme.Foreground, nil)
	}
	return view
}

func (m Model) render() string {
	if m.width < 48 || m.height < 14 {
		return m.styles.error.Render("Paddock needs a terminal at least 48×14")
	}
	header := m.styles.brand.Render("PADDOCK") + "  " + m.renderTabs()
	body := ""
	if !m.loaded {
		body = m.styles.muted.Render("Connecting to the Paddock backend…")
	} else if m.tab == 0 {
		body = m.renderDashboard()
	} else if m.tab == 1 && m.detailOpen {
		body = m.renderSiteDetail()
	} else if m.tab == 1 {
		body = m.renderSites()
	} else if m.serviceForm != "" {
		body = m.renderServiceForm()
	} else if m.serviceDetail {
		body = m.renderServiceDetail()
	} else {
		body = m.renderServices()
	}
	footer := m.renderFooter()
	content := header + "\n\n" + body + "\n" + footer
	if m.logsOpen {
		content = header + "\n\n" + m.renderLogs() + "\n" + footer
	}
	if m.err != "" {
		content += "\n" + m.styles.error.Render("Error: "+truncate(m.err, m.width-7)) + m.styles.muted.Render("  enter dismisses")
	}
	return lipgloss.NewStyle().Padding(1, 2).Width(max(1, m.width-4)).Render(content)
}

func (m Model) renderTabs() string {
	names := []string{"Dashboard", "Sites", "Services"}
	parts := make([]string, len(names))
	for index, name := range names {
		if index == m.tab {
			parts[index] = m.styles.activeTab.Render(name)
		} else {
			parts[index] = m.styles.tab.Render(name)
		}
	}
	return strings.Join(parts, "  ")
}

func (m Model) renderDashboard() string {
	action := "Start All"
	if m.dashboardAllActive() {
		action = "Stop All"
	}
	if m.dashboardBusy {
		verb := "Starting All"
		if !m.dashboardGoal {
			verb = "Stopping All"
		}
		frames := []string{"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}
		action = frames[m.spinnerFrame%len(frames)] + " " + verb
	}
	control := m.styles.worker.Render("[ " + action + " ]")
	if len(m.snapshot.Dashboard.Services) == 0 {
		return control + "\n\n" + m.styles.muted.Render("No Paddock services are configured.")
	}
	groups := map[string][]backend.DashboardService{}
	order := []string{}
	for _, service := range m.snapshot.Dashboard.Services {
		if _, exists := groups[service.Group]; !exists {
			order = append(order, service.Group)
		}
		groups[service.Group] = append(groups[service.Group], service)
	}
	var sections []string
	for _, group := range order {
		lines := []string{}
		for _, service := range groups[group] {
			line := fmt.Sprintf("%s  %-18s %s", m.stateDot(service.State), service.Title, m.styles.muted.Render(service.Detail))
			lines = append(lines, line)
		}
		sections = append(sections, m.renderFieldset(title(group), lines))
	}
	return control + "\n\n" + strings.Join(sections, "\n\n")
}

var serviceKinds = []struct{ kind, label, port string }{
	{"redis", "Redis", "6379"},
	{"mysql", "MySQL", "3306"},
	{"postgres", "PostgreSQL", "5432"},
	{"mailpit", "Mailpit", "1025"},
	{"meilisearch", "Meilisearch", "7700"},
}

func (m Model) selectedService() (backend.ServiceInstance, bool) {
	if m.serviceCursor < 0 || m.serviceCursor >= len(m.snapshot.Services.Instances) {
		return backend.ServiceInstance{}, false
	}
	return m.snapshot.Services.Instances[m.serviceCursor], true
}

func (m Model) selectedServiceDetail() (backend.ServiceInstance, bool) {
	for _, service := range m.snapshot.Services.Instances {
		if service.ID == m.serviceID {
			return service, true
		}
	}
	return backend.ServiceInstance{}, false
}

func (m *Model) clampServiceCursor() {
	last := len(m.snapshot.Services.Instances) - 1
	if last < 0 {
		m.serviceCursor = 0
	} else if m.serviceCursor > last {
		m.serviceCursor = last
	}
}

func (m Model) renderServices() string {
	services := m.snapshot.Services.Instances
	if len(services) == 0 {
		return m.styles.section.Render("Services") + "  " + m.styles.worker.Render("[ Add Service ]") +
			"\n\n" + m.styles.muted.Render("No supporting services have been added.")
	}
	tableWidth := max(40, m.width-8)
	nameWidth := max(10, tableWidth-44)
	header := "  " + siteCell("Name", nameWidth) + "  " + siteCell("Type", 10) + "  " +
		siteCell("Version", 9) + "  " + siteCell("Port", 6) + "  State"
	lines := []string{m.styles.section.Render("Services") + "  " + m.styles.worker.Render("[ Add Service ]"), "", m.styles.muted.Render(truncate(header, tableWidth))}
	for index, service := range services {
		marker := "  "
		if index == m.serviceCursor {
			marker = "› "
		}
		state := m.stateDot(service.State) + " " + title(service.State)
		line := marker + siteCell(service.Label, nameWidth) + "  " + siteCell(service.Type, 10) + "  " +
			siteCell(service.Version, 9) + "  " + siteCell(strconv.Itoa(service.Port), 6) + "  " + state
		line = truncate(line, tableWidth)
		if index == m.serviceCursor {
			line = m.styles.selected.Width(tableWidth).Render(line)
		}
		lines = append(lines, line)
	}
	return strings.Join(lines, "\n")
}

func (m Model) serviceActions() []detailAction {
	service, ok := m.selectedServiceDetail()
	if !ok {
		return nil
	}
	stateAction := "Start"
	if service.State == "active" {
		stateAction = "Stop"
	}
	actions := []detailAction{
		{"service-active", "Status", "State", title(service.State) + " · " + stateAction},
		{"service-autostart", "Status", "Start automatically", onOff(service.Autostart)},
		{"service-port", "Configuration", "Addresses", strings.Join(service.Addresses, "  ")},
		{"service-image", "Configuration", "Image", service.Image},
		{"service-volume", "Configuration", "Data volume", service.Volume},
		{"service-env", "Connection", "Environment", strings.Join(service.Connection, "  ")},
		{"service-logs", "Manage", "Logs", "Open"},
		{"service-edit", "Manage", "Settings", "Edit"},
		{"service-remove", "Danger zone", "Remove service", "Delete data…"},
	}
	if service.DashboardURL != nil {
		actions = append(actions[:7], append(
			[]detailAction{{"service-dashboard", "Manage", "Dashboard", "Open"}},
			actions[7:]...,
		)...)
	}
	return actions
}

func (m Model) renderServiceDetail() string {
	service, ok := m.selectedServiceDetail()
	if !ok {
		return m.styles.error.Render("This service no longer exists.")
	}
	if m.serviceConfirm {
		return m.styles.error.Render("Remove "+service.Label+"?") + "\n\n" +
			"This permanently deletes the service and its data volume " + service.Volume + ".\n\n" +
			m.styles.worker.Render("[ Delete service and data ]") + m.styles.muted.Render("  y/enter confirm · n/esc cancel")
	}
	pending := ""
	if m.serviceBusy {
		frames := []string{"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}
		pending = "  " + m.styles.worker.Render(frames[m.spinnerFrame%len(frames)]+" Working")
	}
	actions := m.serviceActions()
	order := []string{"Status", "Configuration", "Connection", "Manage", "Danger zone"}
	groups := map[string][]string{}
	lineWidth := max(30, m.width-12)
	for index, action := range actions {
		labelWidth := min(20, max(12, lineWidth/4))
		valueWidth := max(6, lineWidth-labelWidth-4)
		value := action.value
		if action.id == "service-active" {
			value = stateGlyph(service.State) + " " + value
		}
		line := "  " + siteCell(action.label, labelWidth) + "  " + siteCell(value, valueWidth)
		if index == m.serviceAction {
			line = m.styles.selected.Width(lineWidth).Render("› " + siteCell(action.label, labelWidth) + "  " + siteCell(value, valueWidth))
		}
		groups[action.section] = append(groups[action.section], line)
	}
	sections := []string{}
	for _, group := range order {
		if len(groups[group]) > 0 {
			sections = append(sections, m.renderFieldsetWidth(group, groups[group], max(40, m.width-8)))
		}
	}
	return m.styles.section.Render(service.Label) + m.styles.muted.Render("  "+title(service.Type)+" "+service.Version) + pending + "\n\n" + strings.Join(sections, "\n\n")
}

func (m *Model) beginAddService() {
	m.serviceForm, m.serviceField, m.serviceType = "add", 0, 0
	m.serviceLabel, m.servicePort, m.serviceBoot = serviceKinds[0].label, serviceKinds[0].port, true
}

func (m *Model) beginEditService(service backend.ServiceInstance) {
	m.serviceForm, m.serviceField = "edit", 0
	m.serviceLabel, m.servicePort, m.serviceBoot = service.Label, strconv.Itoa(service.Port), service.Autostart
	for index, item := range serviceKinds {
		if item.kind == service.Type {
			m.serviceType = index
		}
	}
}

func (m Model) renderServiceForm() string {
	titleText := "Add Service"
	if m.serviceForm == "edit" {
		titleText = "Edit Service"
	}
	fields := []struct{ label, value string }{
		{"Type", serviceKinds[m.serviceType].label}, {"Name", m.serviceLabel},
		{"Port", m.servicePort}, {"Start automatically", onOff(m.serviceBoot)},
	}
	lines := []string{}
	for index, field := range fields {
		marker := "  "
		if index == m.serviceField {
			marker = "› "
		}
		line := marker + siteCell(field.label, 22) + "  " + field.value
		if index == m.serviceField {
			line = m.styles.selected.Width(max(30, m.width-12)).Render(line)
		}
		lines = append(lines, line)
	}
	save := "Save"
	if m.serviceBusy {
		frames := []string{"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}
		save = frames[m.spinnerFrame%len(frames)] + " Saving"
	}
	lines = append(lines, "", m.styles.worker.Render("[ "+save+" ]")+m.styles.muted.Render("  enter saves from any field"))
	return m.styles.section.Render(titleText) + "\n\n" + m.renderFieldsetWidth("Configuration", lines, max(40, m.width-8))
}

func (m Model) renderFieldset(name string, lines []string) string {
	return m.renderFieldsetWidth(name, lines, max(20, m.width-8))
}

func (m Model) renderFieldsetWidth(name string, lines []string, boxWidth int) string {
	label := truncate(name, boxWidth-6)
	topFill := max(1, boxWidth-ansi.StringWidth(label)-5)
	top := m.styles.muted.Render("┌─ ") + m.styles.section.Render(label) +
		m.styles.muted.Render(" "+strings.Repeat("─", topFill)+"┐")
	bottom := m.styles.muted.Render("└" + strings.Repeat("─", boxWidth-2) + "┘")
	innerWidth := boxWidth - 4
	rows := []string{top}
	for _, line := range lines {
		line = truncate(line, innerWidth)
		padding := strings.Repeat(" ", max(0, innerWidth-ansi.StringWidth(line)))
		rows = append(rows, m.styles.muted.Render("│ ")+line+m.styles.muted.Render(padding+" │"))
	}
	rows = append(rows, bottom)
	return strings.Join(rows, "\n")
}

func (m Model) renderSites() string {
	sites := m.filteredSites()
	search := m.renderSiteSearch() + "\n\n"
	if len(sites) == 0 {
		return search + m.styles.muted.Render("No linked sites match.")
	}
	tableWidth := max(40, m.width-8)
	nameWidth := max(7, tableWidth-33)
	header := "  " + siteCell("Name", nameWidth) + "  " + siteCell("PHP", 5) + "  " +
		siteCell("Node", 5) + "  " + siteCell("HTTP(S)", 9) + "  Open"
	start, end := m.siteWindow(len(sites))
	lines := []string{m.styles.muted.Render(truncate(header, tableWidth))}
	for index := start; index < end; index++ {
		site := sites[index]
		marker := "  "
		selected := index == m.cursor
		if selected {
			marker = "› "
		}
		node := "—"
		if site.Node != nil {
			node = *site.Node
		}
		protocol := " HTTP"
		if site.Secured {
			protocol = " HTTPS"
		}
		open := "Open"
		if !selected {
			open = m.styles.accent.Underline(true).Render(open)
		}
		line := marker + siteCell(site.Name, nameWidth) + "  " + siteCell(site.PHP, 5) + "  " +
			siteCell(node, 5) + "  " + siteCell(protocol, 9) + "  " + open
		line = truncate(line, tableWidth)
		if selected {
			line = m.styles.selected.Width(tableWidth).Render(line)
		}
		lines = append(lines, line)
	}
	return search + strings.Join(lines, "\n")
}

func (m Model) renderSiteSearch() string {
	boxWidth := max(40, m.width-8)
	innerWidth := boxWidth - 4
	value := m.filter
	if value == "" && !m.filtering {
		value = m.styles.muted.Render("Type / to search sites")
	} else if m.filtering {
		value += m.styles.accent.Render("_")
	}
	value = truncate(value, innerWidth)
	padding := strings.Repeat(" ", max(0, innerWidth-ansi.StringWidth(value)))
	topFill := max(1, boxWidth-len("Search")-5)
	top := m.styles.muted.Render("┌─ ") + m.styles.section.Render("Search") +
		m.styles.muted.Render(" "+strings.Repeat("─", topFill)+"┐")
	middle := m.styles.muted.Render("│ ") + value + m.styles.muted.Render(padding+" │")
	bottom := m.styles.muted.Render("└" + strings.Repeat("─", boxWidth-2) + "┘")
	return strings.Join([]string{top, middle, bottom}, "\n")
}

func (m Model) siteWindow(siteCount int) (int, int) {
	availableHeight := max(2, m.height-16)
	start := 0
	if m.cursor >= availableHeight {
		start = m.cursor - availableHeight + 1
	}
	return start, min(siteCount, start+availableHeight)
}

func siteCell(value string, width int) string {
	value = truncate(value, width)
	return value + strings.Repeat(" ", max(0, width-ansi.StringWidth(value)))
}

func (m Model) renderSiteDetail() string {
	site, ok := m.detailSelectedSite()
	if !ok {
		return m.styles.error.Render("This site is no longer linked.")
	}
	actions := m.detailActions()
	groups := map[string][]string{"Details": {}, "Workers": {}}
	for index, action := range actions {
		marker := "  "
		if index == m.detailCursor {
			marker = "› "
		}
		lineWidth := max(16, m.detailBoxWidth()-4)
		labelWidth := min(18, max(10, lineWidth/3))
		valueWidth := max(4, lineWidth-labelWidth-4)
		value := siteCell(action.value, valueWidth)
		ledState := ""
		if action.id == "queue-active" {
			ledState = site.QueueState
			if !site.QueueConfigured {
				ledState = "inactive"
			}
		} else if action.id == "reverb-active" {
			ledState = site.ReverbState
			if !site.ReverbConfigured {
				ledState = "inactive"
			}
		}
		if ledState != "" {
			value = m.stateDot(ledState) + " " + siteCell(action.value, max(1, valueWidth-2))
		}
		if action.id == "url" || action.id == "path" || action.id == "terminal" || action.id == "zed" || action.id == "nginx-edit" || action.id == "nginx-reload" || strings.HasSuffix(action.id, "-logs") {
			value = styledDetailValue(action.value, valueWidth, m.styles.accent.Underline(true))
		}
		line := marker + siteCell(action.label, labelWidth) + "  " + value
		if index == m.detailCursor {
			// Avoid a nested link reset interrupting the selected background.
			plainValue := siteCell(action.value, valueWidth)
			if ledState != "" {
				left := marker + siteCell(action.label, labelWidth) + "  "
				right := " " + siteCell(action.value, max(1, valueWidth-2))
				line = m.styles.selected.Render(left) +
					m.stateStyle(ledState).Inherit(m.styles.selected).Render(stateGlyph(ledState)) +
					m.styles.selected.Width(valueWidth-1).Render(right)
				groups[action.section] = append(groups[action.section], line)
				continue
			}
			line = marker + siteCell(action.label, labelWidth) + "  " + plainValue
			line = m.styles.selected.Width(lineWidth).Render(line)
		}
		groups[action.section] = append(groups[action.section], line)
	}
	boxWidth := m.detailBoxWidth()
	details := m.renderFieldsetWidth("Details", groups["Details"], boxWidth)
	workers := ""
	if len(groups["Workers"]) > 0 {
		workers = m.renderFieldsetWidth("Workers", groups["Workers"], boxWidth)
	}
	body := details
	if workers != "" {
		if m.width >= 80 {
			body = lipgloss.JoinHorizontal(lipgloss.Top, details, "  ", workers)
		} else {
			body += "\n\n" + workers
		}
	}
	return m.styles.section.Render(site.Host) + m.styles.muted.Render("  "+site.Root) + "\n\n" + body
}

func stateGlyph(state string) string {
	if state == "active" || state == "failed" || state == "activating" {
		return "●"
	}
	return "○"
}

func styledDetailValue(value string, width int, style lipgloss.Style) string {
	value = truncate(value, width)
	return style.Render(value) + strings.Repeat(" ", max(0, width-ansi.StringWidth(value)))
}

func (m Model) detailBoxWidth() int {
	if m.width >= 80 {
		return max(32, (m.width-10)/2)
	}
	return max(40, m.width-8)
}

func (m Model) workerSummary(site backend.Site) string {
	queue := workerLabel("queue", site.QueueAvailable, site.QueueState, site.QueueAutostart)
	reverb := workerLabel("reverb", site.ReverbAvailable, site.ReverbState, site.ReverbAutostart)
	if m.worker == 0 {
		queue = m.styles.worker.Render("[" + queue + "]")
	} else {
		reverb = m.styles.worker.Render("[" + reverb + "]")
	}
	return queue + "  " + reverb
}

func workerLabel(name string, available bool, state string, autostart bool) string {
	if !available {
		return title(name) + ": n/a"
	}
	boot := ""
	if autostart {
		boot = " ↻"
	}
	return fmt.Sprintf("%s: %s%s", title(name), state, boot)
}

func (m Model) renderLogs() string {
	height := m.logHeight()
	start := min(m.logOffset, max(0, len(m.logs)-height))
	end := min(len(m.logs), start+height)
	lines := m.logs[start:end]
	if len(lines) == 0 {
		lines = []string{"No journal lines were returned."}
	}
	titleLine := m.styles.section.Render(m.logTitle) + m.styles.muted.Render("  j/k scroll · esc close")
	return titleLine + "\n\n" + m.styles.log.Render(strings.Join(lines, "\n"))
}

func (m Model) logHeight() int { return max(3, m.height-11) }

func (m Model) renderFooter() string {
	help := "space start/stop all · tab/shift+tab sections · 1/2/3 jump · r refresh · q quit"
	if m.tab == 1 {
		help = "↑/↓ select · enter details · o open · / filter · tab sections · q quit"
		if m.detailOpen {
			help = "↑/↓ select · enter activate · ←/→ change version · esc back · o open site"
		}
	}
	if m.tab == 2 {
		help = "↑/↓ select · enter details · a add service · tab sections · q quit"
		if m.serviceDetail {
			help = "↑/↓ select · enter activate · esc back"
		}
		if m.serviceForm != "" {
			help = "tab/↑/↓ field · ←/→ change · enter save · esc cancel"
		}
		if m.serviceConfirm {
			help = "y/enter delete service and data · n/esc cancel"
		}
	}
	rows := []string{"", m.styles.muted.Render(truncate(help, m.width-8))}
	if m.status != "" {
		rows = append(rows, m.styles.good.Render("✓ "+truncate(m.status, m.width-10)))
	}
	return strings.Join(rows, "\n")
}

func dismissToast(identifier int) tea.Cmd {
	return tea.Tick(toastDuration, func(time.Time) tea.Msg {
		return toastExpiredMsg(identifier)
	})
}

func spinnerTick(generation int) tea.Cmd {
	return tea.Tick(80*time.Millisecond, func(time.Time) tea.Msg {
		return spinnerTickMsg(generation)
	})
}

func (m Model) stateDot(state string) string {
	return m.stateStyle(state).Render(stateGlyph(state))
}

func (m Model) stateStyle(state string) lipgloss.Style {
	switch state {
	case "active":
		return m.styles.good
	case "failed":
		return m.styles.error
	case "activating":
		return m.styles.warn
	default:
		return m.styles.muted
	}
}

func secureLabel(secured bool) string {
	if secured {
		return "https"
	}
	return "http"
}
func title(value string) string {
	if value == "" {
		return value
	}
	return strings.ToUpper(value[:1]) + value[1:]
}
func truncate(value string, width int) string {
	if width <= 0 {
		return ""
	}
	return ansi.Truncate(value, width, "…")
}
func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}
