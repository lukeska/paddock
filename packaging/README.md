# Packaging and development release status

The release recipe is `paddock 0.1.8-1`; local development builds deliberately
use the stable revision `paddock 0.1.8-25`.

## What the package owns

`makepkg` builds it, and `check()` runs the whole unit suite, so a
package that builds is a package whose tests passed. It installs the Python
package, `/usr/bin/paddock`, `/usr/bin/paddock-tui`, its TUI desktop entry,
icons and AppStream metadata, the fixed root helpers under `/usr/lib/paddock`, the
artifact index, and the state-schema and project-file references under
`/usr/share/doc/paddock`.

It deliberately does **not** own the Omarchy plugin: ADR 0008 reserves
`~/.config/omarchy/plugins` for Omarchy, and pacman must not write there.

CI installs each newly built main package into a second, fresh Arch container.
That job resolves the package's declared runtime dependencies with `pacman`,
checks package ownership and file integrity, then runs the installed CLI as an
unprivileged user from outside the checkout. This catches missing package files,
undeclared dependencies, accidental source-tree imports, and install hooks that
mutate user or system integration before `paddock setup`.

Runtime dependencies include `python`, `python-yaml`, `nginx`, `dnsmasq`,
`mkcert`, `nss`, `p11-kit`, `networkmanager`, `polkit`, `podman`, `curl`, and
`wl-clipboard`. `python-yaml` reads `paddock.yml`; the terminal UI is a static
Go binary. Note that every hard dependency must exist
wherever the package is **built**, not only where it runs — `makepkg` resolves
runtime dependencies before building, which broke both CI and the local build
when `podman` was added.

## Verified locally

- Clean install, upgrade, rollback, and forward upgrade on Omarchy, with user
  configuration, CA material, DNS, and running services surviving each.
- `setup`/`uninstall`/`setup` regenerates system integration without losing
  user-owned state.
- `pacman -R` tears down generated system integration even when a user skips
  `paddock uninstall`, using a root-owned installation record, while projects,
  configuration, runtimes, logs, cache, and the private CA survive. Reinstall
  plus setup restored a healthy stack without recreating user state.
- The installed package downloads published PHP runtimes from the public GitHub
  prerelease. PHP 8.4 and 8.5 have additionally survived a live reboot test
  with zero restarts.
- Redis, MySQL and PostgreSQL run as rootless containers in user units and
  return after a reboot.
- The optional Omarchy plugin passes `omarchy plugin validate`.
- `./tests/acceptance/run.sh` passes every check against the live system. Run
  it after any packaging change; it is faster and more honest than reading
  state by hand.

## Published runtime prerelease

The packaged artifact index points at the `php-2026.09.20` GitHub prerelease.
Paddock-owned PHP 8.0 through 8.5 x86_64 archives, checksums, file-level SPDX
inventories, ABI records, provenance, and build logs are public. Every archive
was built and probed on a GitHub-hosted runner, attested to commit `cf472ed`,
downloaded again through its public release URL, and checked against the hash
in the packaged index.

## Remaining publication gates

The application-release workflow publishes a checksum-pinned GitHub package
from `v<semver>` tags. The maintainer then signs the exact workflow-built
package and source archive following `release/PROMOTION.md`. Release `v0.1.2`
is the first release carrying both detached signatures. The published primary
fingerprint is `AB3611DC044DE36844055E9AC1A41BDC59DCEA60`.

1. Decide AUR versus a Paddock repository, and how the optional plugin is
   distributed — three options are recorded in the private plan notes, and the
   constraint is that `omarchy plugin add` clones a repository *root* while the
   plugin lives in a subdirectory here.
2. Add the signed source and `validpgpkeys` contract to the AUR recipe once AUR
   publication is available.
3. Test a clean Omarchy install from public URLs, then update, rollback,
   uninstall, and reinstall.

CI builds GitHub-attested PHP release candidates without publishing them. The
individual dispatch path has been validated for every supported PHP minor;
promotion remains a separate manual step.

Public StaticPHP artifacts are not an acceptable shortcut: the tested artifacts
omit `intl` and lack Paddock's provenance chain.
