package tui

import (
	"fmt"
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
	SetActive(site, worker string, active bool) (backend.OperationResult, error)
	SetAutostart(site, worker string, enabled bool) (backend.OperationResult, error)
	Logs(site, worker string, lines int) (backend.LogsResult, error)
	Close() error
}

type Model struct {
	api           API
	python        string
	snapshot      backend.Snapshot
	loaded        bool
	width         int
	height        int
	tab           int
	cursor        int
	worker        int
	filtering     bool
	filter        string
	logs          []string
	logsOpen      bool
	logOffset     int
	busy          bool
	dashboardBusy bool
	dashboardGoal bool
	spinnerFrame  int
	status        string
	toastID       int
	logTitle      string
	err           string
	generation    int
	terminalDark  bool
	styles        styles
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
type logsMsg struct {
	site, worker string
	lines        []string
	err          error
}
type tickMsg time.Time
type toastExpiredMsg int
type spinnerTickMsg int

func New(python string) Model {
	return Model{
		python: python, width: 80, height: 24, generation: 1,
		terminalDark: true, styles: newStyles(true, nil),
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
	case spinnerTickMsg:
		if m.dashboardBusy && int(msg) == m.generation {
			m.spinnerFrame++
			return m, spinnerTick(m.generation)
		}
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
	case tea.KeyPressMsg:
		return m.handleKey(msg)
	}
	return m, nil
}

func (m Model) handleKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	key := msg.String()
	if key == "ctrl+c" || (!m.filtering && key == "q") {
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
	if m.err != "" && (key == "esc" || key == "enter") {
		m.err = ""
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
		m.tab = (m.tab + 1) % 2
		m.cursor = 0
	case "shift+tab":
		m.tab = (m.tab + 2 - 1) % 2
		m.cursor = 0
	case "1":
		m.tab = 0
		m.cursor = 0
	case "2":
		m.tab = 1
		m.cursor = 0
	case "j", "down":
		if m.tab == 1 && m.cursor < len(m.filteredSites())-1 {
			m.cursor++
		}
	case "k", "up":
		if m.tab == 1 && m.cursor > 0 {
			m.cursor--
		}
	case "/":
		if m.tab == 1 {
			m.filtering = true
		}
	case "left", "h":
		if m.tab == 1 {
			m.worker = 0
		}
	case "right", "l":
		if m.tab == 1 {
			m.worker = 1
		}
	case " ", "space":
		if m.tab == 0 {
			return m.mutateDashboard()
		}
		return m.mutateActive()
	case "a":
		return m.mutateAutostart()
	case "g":
		if m.tab == 1 {
			m.cursor = 0
		}
	case "G":
		if m.tab == 1 {
			m.cursor = max(0, len(m.filteredSites())-1)
		}
	case "o": // Browser launching remains explicit in the desktop UI.
	case "L", "shift+l":
		return m.openLogs()
	}
	return m, nil
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

func (m Model) selected() (backend.Site, bool) {
	sites := m.filteredSites()
	if m.cursor < 0 || m.cursor >= len(sites) {
		return backend.Site{}, false
	}
	return sites[m.cursor], true
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
	} else {
		body = m.renderSites()
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
	names := []string{"Dashboard", "Sites"}
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
	if len(m.snapshot.Services.Instances) > 0 {
		lines := []string{}
		for _, instance := range m.snapshot.Services.Instances {
			lines = append(lines, fmt.Sprintf("%s  %-18s %s:%d", m.stateDot(instance.State), instance.Label, instance.Type, instance.Port))
		}
		sections = append(sections, m.renderFieldset("Service instances", lines))
	}
	return control + "\n\n" + strings.Join(sections, "\n\n")
}

func (m Model) renderFieldset(name string, lines []string) string {
	boxWidth := max(20, m.width-8)
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
	filter := ""
	if m.filtering || m.filter != "" {
		cursor := ""
		if m.filtering {
			cursor = "_"
		}
		filter = m.styles.accent.Render("Filter: ") + m.filter + cursor + "\n\n"
	}
	if len(sites) == 0 {
		return filter + m.styles.muted.Render("No linked sites match.")
	}
	availableHeight := max(3, m.height-11)
	start := 0
	if m.cursor >= availableHeight {
		start = m.cursor - availableHeight + 1
	}
	end := min(len(sites), start+availableHeight)
	lines := make([]string, 0, end-start)
	for index := start; index < end; index++ {
		site := sites[index]
		marker := "  "
		style := lipgloss.NewStyle()
		if index == m.cursor {
			marker = "› "
			style = m.styles.selected
		}
		node := "—"
		if site.Node != nil {
			node = *site.Node
		}
		if m.width >= 100 {
			workers := m.workerSummary(site)
			line := fmt.Sprintf("%s%-20s PHP %-5s Node %-4s %-10s %s", marker, site.Host, site.PHP, node, secureLabel(site.Secured), workers)
			lines = append(lines, style.Render(truncate(line, m.width-8)))
		} else {
			lines = append(lines, style.Render(fmt.Sprintf("%s%s  PHP %s · Node %s", marker, site.Host, site.PHP, node)))
			if index == m.cursor {
				lines = append(lines, "    "+m.workerSummary(site))
			}
		}
	}
	return filter + strings.Join(lines, "\n")
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
	help := "space start/stop all · tab/shift+tab sections · 1/2 jump · r refresh · q quit"
	if m.tab == 1 {
		help = "↑/↓ site · ←/→ worker · space start/stop · a autostart · L logs · / filter"
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
	switch state {
	case "active":
		return m.styles.good.Render("●")
	case "failed":
		return m.styles.error.Render("●")
	case "activating":
		return m.styles.warn.Render("●")
	default:
		return m.styles.muted.Render("○")
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
