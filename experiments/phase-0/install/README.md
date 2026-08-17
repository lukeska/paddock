# Experiment 0.12: Installation and Omarchy Integration

## Status

- State: passed
- Core boundary: Arch package
- Optional UI boundary: Omarchy shell plugin

`omarchy plugin` explicitly manages shell/bar plugins. It is not the core
installer for binaries, system units, DNS, PHP runtimes, or trust. Paddock is
therefore packaged as an Arch package with a PATH-visible `paddock` command.
An optional shell plugin may call that stable CLI but never owns service state.

The package rehearsal uses `makepkg`, local package artifacts, and pacman. User
instructions use `omarchy pkg add` for repository dependencies. No file under
`/usr/share/omarchy` is modified.

## Results so far

- Package 0.0.1 installed with pacman and appeared in a pristine shell without
  editing shell startup files.
- Upgrade to 0.0.2 added an observable command while preserving ownership.
- Downgrade to the retained 0.0.1 artifact removed that command cleanly.
- `omarchy pkg drop paddock-phase0` removed the package and binary while
  preserving shared dependencies, projects, and the explicitly retained CA.
- Reinstall of 0.0.2 restored clean-shell discovery.
- The optional plugin validated, installed/enabled through `omarchy plugin add`,
  updated independently from Git, and was removed/unloaded completely.
- Pacman never owned files under `~/.config/omarchy/plugins`; Omarchy never
  owned `/usr/bin/paddock`.

`bootstrap.sh` provides one user-facing operation while retaining those two
ownership domains internally. It must run as the desktop user and requests sudo
only for package installation.

The unified bootstrap and matching cleanup both passed. Final verification
found no package, binary, plugin directory, catalog entry, or temporary Git
origin. Shared dependencies, CA trust, projects, and Omarchy-owned files were
preserved.

Decisions: [ADR 0007](../../../docs/adr/0007-installation-boundary.md) and
[ADR 0008](../../../docs/adr/0008-omarchy-integration.md).
