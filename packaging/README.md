# Development release status

## Completed locally

- Arch package `paddock 0.1.0-4` builds with `makepkg`.
- The package runs all 36 tests during `check()`.
- Extracted-package CLI and package ownership manifests were inspected.
- Clean install of `0.1.0-1`, upgrade to `0.1.0-2`, rollback to `0.1.0-1`,
  and forward upgrade to `0.1.0-2` passed on Omarchy.
- User configuration, CA material, DNS, and active services survived package
  rollback and upgrade.
- Setup/uninstall/setup regenerated system integration without losing user data.
- The optional Omarchy plugin passes `omarchy plugin validate` and owns no
  canonical state.

## Published runtime prerelease

The package artifact index points to the `php-2026.08.18` GitHub prerelease.
Paddock-owned PHP 8.4.23 and 8.5.8 x86_64 archives, checksums, file-level SPDX
inventories, ABI records, and unsigned provenance are public and have passed a
fresh production-installer download test.

## Remaining publication gates

The local Arch package remains intentionally unsigned and is not yet a
supported public release. Configure GitHub Actions attestations and the release
GPG key, then sign the Arch package, repository database, source archive,
artifact index, and PHP artifacts according to the release policy.

Public StaticPHP artifacts are not an acceptable shortcut because the tested
artifacts omitted `intl` and lack the selected Paddock provenance chain.
