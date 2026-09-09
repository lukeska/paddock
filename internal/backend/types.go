package backend

type Snapshot struct {
	ProtocolVersion int                      `json:"protocol_version"`
	Dashboard       DashboardSnapshot        `json:"dashboard"`
	Services        ServiceInstancesSnapshot `json:"services"`
	Sites           LinkedSitesSnapshot      `json:"sites"`
	PHP             PHPVersionsSnapshot      `json:"php"`
	Node            NodeVersionsSnapshot     `json:"node"`
	Theme           *ThemePalette            `json:"theme"`
}

type PHPVersion struct {
	Minor        string  `json:"minor"`
	Release      string  `json:"release"`
	Architecture string  `json:"architecture"`
	Installed    bool    `json:"installed"`
	Available    bool    `json:"available"`
	Path         *string `json:"path"`
}

type PHPVersionsSnapshot struct {
	Versions     []PHPVersion `json:"versions"`
	Architecture string       `json:"architecture"`
}

type PHPInstallResult struct {
	OK       bool                `json:"ok"`
	Summary  string              `json:"summary"`
	Detail   *string             `json:"detail"`
	Snapshot PHPVersionsSnapshot `json:"snapshot"`
}

type NodeVersion struct {
	Major        string  `json:"major"`
	Release      string  `json:"release"`
	Architecture string  `json:"architecture"`
	Installed    bool    `json:"installed"`
	Available    bool    `json:"available"`
	Path         *string `json:"path"`
}

type NodeVersionsSnapshot struct {
	Versions     []NodeVersion `json:"versions"`
	Architecture string        `json:"architecture"`
}

type NodeInstallResult struct {
	OK       bool                 `json:"ok"`
	Summary  string               `json:"summary"`
	Detail   *string              `json:"detail"`
	Snapshot NodeVersionsSnapshot `json:"snapshot"`
}

type ThemePalette struct {
	Mode              string  `json:"mode"`
	Accent            *string `json:"accent"`
	Selection         *string `json:"selection"`
	Muted             *string `json:"muted"`
	Background        *string `json:"background"`
	DarkBackground    *string `json:"dark_background"`
	DarkerBackground  *string `json:"darker_background"`
	LighterBackground *string `json:"lighter_background"`
	Foreground        *string `json:"foreground"`
	DarkForeground    *string `json:"dark_foreground"`
	LightForeground   *string `json:"light_foreground"`
	BrightForeground  *string `json:"bright_foreground"`
	Red               *string `json:"red"`
	Yellow            *string `json:"yellow"`
	Orange            *string `json:"orange"`
	Green             *string `json:"green"`
	Cyan              *string `json:"cyan"`
	Blue              *string `json:"blue"`
	Magenta           *string `json:"magenta"`
	Brown             *string `json:"brown"`
}

type DashboardSnapshot struct {
	Services []DashboardService `json:"services"`
}

type DashboardService struct {
	Key        string   `json:"key"`
	Title      string   `json:"title"`
	Group      string   `json:"group"`
	State      string   `json:"state"`
	Detail     string   `json:"detail"`
	Configured bool     `json:"configured"`
	Connection []string `json:"connection"`
	Port       *int     `json:"port"`
	Autostart  bool     `json:"autostart"`
}

type ServiceInstancesSnapshot struct {
	Instances []ServiceInstance `json:"instances"`
}

type ServiceOperationResult struct {
	OK       bool                     `json:"ok"`
	Summary  string                   `json:"summary"`
	Detail   *string                  `json:"detail"`
	Snapshot ServiceInstancesSnapshot `json:"snapshot"`
}

type ServiceLogsResult struct {
	OK      bool     `json:"ok"`
	Summary string   `json:"summary"`
	Lines   []string `json:"lines"`
	Detail  *string  `json:"detail"`
}

type ServiceInstance struct {
	ID           string   `json:"id"`
	Type         string   `json:"type"`
	Label        string   `json:"label"`
	Image        string   `json:"image"`
	Version      string   `json:"version"`
	Port         int      `json:"port"`
	Volume       string   `json:"volume"`
	State        string   `json:"state"`
	Autostart    bool     `json:"autostart"`
	Connection   []string `json:"connection"`
	Addresses    []string `json:"addresses"`
	DashboardURL *string  `json:"dashboard_url"`
}

type LinkedSitesSnapshot struct {
	Sites        []Site   `json:"sites"`
	PHPVersions  []string `json:"php_versions"`
	NodeVersions []string `json:"node_versions"`
}

type Site struct {
	Name             string  `json:"name"`
	Host             string  `json:"host"`
	URL              string  `json:"url"`
	PHP              string  `json:"php"`
	Secured          bool    `json:"secured"`
	Root             string  `json:"root"`
	Node             *string `json:"node"`
	ReverbAvailable  bool    `json:"reverb_available"`
	ReverbConfigured bool    `json:"reverb_configured"`
	ReverbState      string  `json:"reverb_state"`
	ReverbAutostart  bool    `json:"reverb_autostart"`
	ReverbPort       *int    `json:"reverb_port"`
	QueueAvailable   bool    `json:"queue_available"`
	QueueConfigured  bool    `json:"queue_configured"`
	QueueState       string  `json:"queue_state"`
	QueueAutostart   bool    `json:"queue_autostart"`

	// Project type and the directory served for it.
	Type         string `json:"type"`
	DocumentRoot string `json:"document_root"`

	// The viewer's own nginx fragment. Always addressable so the UI can
	// offer to create one; CustomConfigPresent says whether it exists yet.
	CustomConfig        string `json:"custom_config"`
	CustomConfigPresent bool   `json:"custom_config_present"`
	NginxConfigError    bool   `json:"nginx_config_error"`

	// A fragment the project ships. Status is one of none, missing,
	// pending, changed, trusted; anything but trusted means nothing from
	// the repository is being served.
	ProjectConfig       *string `json:"project_config"`
	ProjectConfigStatus string  `json:"project_config_status"`
}

type OperationResult struct {
	OK       bool                `json:"ok"`
	Summary  string              `json:"summary"`
	Detail   *string             `json:"detail"`
	Snapshot LinkedSitesSnapshot `json:"snapshot"`
}

type DashboardOperationResult struct {
	OK       bool              `json:"ok"`
	Summary  string            `json:"summary"`
	Detail   *string           `json:"detail"`
	Snapshot DashboardSnapshot `json:"snapshot"`
}

type LogsResult struct {
	Lines []string `json:"lines"`
}
