# ADR 0007: Installation, Update, and Removal Boundary

- Status: accepted for implementation
- Date: 2026-08-17
- Experiment: [Installation rehearsal](../../experiments/phase-0/install/README.md)

## Decision

Distribute Paddock as a signed Arch package, initially through the AUR and
later through a repository if available. Omarchy-facing instructions use
`omarchy pkg aur add paddock` or `omarchy pkg add paddock`; pacman remains
the source of truth for installed files and versions.

The package installs the CLI, fixed helpers, templates, unit definitions, and
an ownership manifest. Machine-specific setup is a separate explicit
`paddock setup` transaction that previews privileged DNS, trust, and systemd
changes before requesting authentication. Daily project, PHP, certificate, and
diagnostic commands run without elevation.

Updates install a complete versioned package and validate compatibility before
activating new generated state. Retain the previous package/runtime generation
until health checks pass. Rollback reinstalls the retained package and restores
the last-known-good generated configuration atomically.

Uninstall first disables/removes Paddock system integration, then removes the
package. Shared dependencies are preserved unless explicitly requested.
Projects are never removed. Config, runtimes, logs, cache, and the CA private
key are separate retention choices; public trust removal is explicit and
fingerprint-scoped.

## Evidence

Package 0.0.1 installed, was clean-shell discoverable, upgraded to 0.0.2,
downgraded to the retained 0.0.1 artifact, removed through `omarchy pkg drop`,
and reinstalled as 0.0.2. Pacman ownership remained correct throughout. Removal
preserved Caddy, dnsmasq, mkcert, CA trust, projects, and unrelated Omarchy
state. Checksummed local source and package contents validated successfully.

