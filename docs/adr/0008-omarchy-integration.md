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

## Evidence

The schema-valid bar plugin installed/enabled from Git, updated independently,
and removed/unloaded through Omarchy. It was third-party and not package-owned.
The CLI was package-owned and worked without the plugin. A unified bootstrap
installed both in one operation; unified cleanup removed both while preserving
shared dependencies and unrelated state. No `/usr/share/omarchy` file changed.

