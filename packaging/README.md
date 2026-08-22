# Packaging and development release status

Current at revision `paddock 0.1.0-16`.

## What the package owns

`makepkg` builds it, and `check()` runs the whole unit suite — 194 tests — so a
package that builds is a package whose tests passed. It installs the Python
package, `/usr/bin/paddock`, the fixed root helpers under `/usr/lib/paddock`,
the artifact index, and the state-schema and project-file references under
`/usr/share/doc/paddock`.

It deliberately does **not** own the Omarchy plugin: ADR 0008 reserves
`~/.config/omarchy/plugins` for Omarchy, and pacman must not write there.

Runtime dependencies are `python`, `python-yaml`, `caddy`, `dnsmasq`, `mkcert`,
`nss`, `p11-kit`, `networkmanager`, `polkit`, and `podman`. `python-yaml` is the
only Python dependency, for reading `paddock.yml`; everything else in the
codebase is standard library. Note that every hard dependency must exist
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
- The installed package downloads PHP 8.4.23 and 8.5.8 from the public GitHub
  prerelease and both survive a reboot with zero restarts.
- Redis, MySQL and PostgreSQL run as rootless containers in user units and
  return after a reboot.
- The optional Omarchy plugin passes `omarchy plugin validate`.
- `./tests/acceptance/run.sh` passes every check against the live system. Run
  it after any packaging change; it is faster and more honest than reading
  state by hand.

## Published runtime prerelease

The packaged artifact index points at the `php-2026.08.18` GitHub prerelease.
Paddock-owned PHP 8.4.23 and 8.5.8 x86_64 archives, checksums, file-level SPDX
inventories, ABI records, and unsigned provenance are public and have passed a
fresh production-installer download test.

## Remaining publication gates

The package is intentionally unsigned and is not a supported public release.

1. Generate the release key offline per ADR 0009 and publish its fingerprint.
   No key exists yet.
2. Sign the package and source archive, following `release/PROMOTION.md`.
3. Decide AUR versus a Paddock repository, and how the optional plugin is
   distributed — three options are recorded in the private plan notes, and the
   constraint is that `omarchy plugin add` clones a repository *root* while the
   plugin lives in a subdirectory here.
4. Test a clean Omarchy install from public URLs, then update, rollback,
   uninstall, and reinstall.

CI builds GitHub-attested PHP release candidates without publishing them. The
`runtime=all` dispatch remains unvalidated after its `GITHUB_TOKEN` fix.

Public StaticPHP artifacts are not an acceptable shortcut: the tested artifacts
omit `intl` and lack Paddock's provenance chain.
