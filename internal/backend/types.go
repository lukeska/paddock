package backend

type Snapshot struct {
	ProtocolVersion int                      `json:"protocol_version"`
	Dashboard       DashboardSnapshot        `json:"dashboard"`
	Services        ServiceInstancesSnapshot `json:"services"`
	Sites           LinkedSitesSnapshot      `json:"sites"`
	Theme           *ThemePalette            `json:"theme"`
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

type ServiceInstance struct {
	ID         string   `json:"id"`
	Type       string   `json:"type"`
	Label      string   `json:"label"`
	Image      string   `json:"image"`
	Version    string   `json:"version"`
	Port       int      `json:"port"`
	Volume     string   `json:"volume"`
	State      string   `json:"state"`
	Autostart  bool     `json:"autostart"`
	Connection []string `json:"connection"`
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
