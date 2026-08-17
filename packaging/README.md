# Development release status

## Completed locally

- Arch package `paddock 0.1.0-2` builds with `makepkg`.
- The package runs all 35 tests during `check()`.
- Extracted-package CLI and package ownership manifests were inspected.
- Clean install of `0.1.0-1`, upgrade to `0.1.0-2`, rollback to `0.1.0-1`,
  and forward upgrade to `0.1.0-2` passed on Omarchy.
- User configuration, CA material, DNS, and active services survived package
  rollback and upgrade.
- Setup/uninstall/setup regenerated system integration without losing user data.
- The optional Omarchy plugin passes `omarchy plugin validate` and owns no
  canonical state.

## Publication gates

The local package is intentionally unsigned and the installed artifact index is
intentionally empty. It is not a publishable release until both gates close:

1. Build and host Paddock-owned PHP artifacts for every advertised
   minor/architecture, including the accepted baseline extensions and release
   metadata (SHA-256, SBOM, build log, and attestation).
2. Configure the release GPG key and sign the Arch package, repository database,
   source archive, artifact index, and PHP artifacts according to the release
   policy.

Public StaticPHP artifacts are not an acceptable shortcut because the tested
artifacts omitted `intl` and lack the selected Paddock provenance chain.
