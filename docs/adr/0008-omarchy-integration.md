# ADR 0008: Omarchy Integration

- Status: accepted for implementation
- Date: 2026-08-17
- Experiment: [Installation rehearsal](../../experiments/phase-0/install/README.md)

## Decision

The Arch package and `paddock` CLI are the required product. An Omarchy Shell
plugin is an optional UI companion that calls the stable CLI. It never installs
packages, owns services, modifies DNS/trust, or stores canonical project state.

Manage the companion only through `omarchy plugin add/update/enable/remove` in
`~/.config/omarchy/plugins`. Pacman must not own that directory. Never modify
`/usr/share/omarchy`; it belongs to Omarchy and may be replaced by updates.

A single bootstrap command may install both components, but it must disclose
the two ownership domains. It runs as the desktop user, requests sudo only for
the package operation, then delegates plugin installation to Omarchy. Plugin
failure does not invalidate or silently remove a working core package.

The initial public command is `paddock`. Do not depend on an undocumented
`omarchy-paddock` discovery convention. Shell UI must degrade cleanly when
Omarchy Shell is unavailable, while the CLI remains fully functional.

## Amendment: what the companion presents, and how

Dated 2026-08-22. The original decision fixed ownership and installation but
said nothing about content, refresh, or theming. The plugin has grown from a
button that shelled `paddock status` into a terminal into a real status
surface, so those are recorded now.

**The interface is `paddock report`.** One JSON document carrying its own
`schema_version`, not scraped TSV. The plugin parses that and nothing else,
and it never reads Paddock's state files directly, which keeps the CLI the
only contract and leaves state ownership where this ADR put it.

**Polling belongs to a `service`-kind singleton.** A bar widget is
instantiated once per monitor, so a timer in the widget would run one
subprocess per screen per tick. The service is instantiated once and the
widget reads it through `bar.shell.serviceFor`. Enabling the widget into a bar
section enables the service too, so it stays a single step for the user.

**Scope is viewing plus non-mutating navigation.** Opening a site, or opening
`doctor` or `logs` in a terminal. Nothing in the panel starts, stops, or
configures anything, which keeps "never owns services" concrete rather than
aspirational. Service control from the panel is a later decision, and would
need confirmation and in-panel error reporting before it is worth having.

**Theming is by binding, not by reading.** Colours come from the `Color` and
`Style` singletons; `omarchy-theme-set` pushes a palette over IPC and QML
bindings re-theme the plugin live. The plugin never reads a theme file, and a
colour literal in plugin QML is a defect a test now rejects.

**Degradation is specified.** A missing `paddock`, a non-zero exit, or
unparseable output yields a muted `unknown` state and an explanation, with the
last good snapshot retained. This ADR already required the shell UI to degrade
cleanly; this is what that means in practice.

## Evidence

The schema-valid bar plugin installed/enabled from Git, updated independently,
and removed/unloaded through Omarchy. It was third-party and not package-owned.
The CLI was package-owned and worked without the plugin. A unified bootstrap
installed both in one operation; unified cleanup removed both while preserving
shared dependencies and unrelated state. No `/usr/share/omarchy` file changed.

