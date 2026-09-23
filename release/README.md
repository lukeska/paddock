# Application releases

Application releases are created only from immutable `v<semver>` tags. The
tag-triggered workflow validates that the tag, Python package, module, and Arch
recipe all name the same version before it can publish anything.

The workflow then:

1. runs the complete Python, lab-orchestration, and Go TUI test suites;
2. creates the deterministic `paddock-<version>.tar.gz` source archive;
3. verifies that archive against the SHA-256 pinned in the release `PKGBUILD`;
4. builds `paddock-<version>-1-x86_64.pkg.tar.zst` in a clean Arch container;
5. installs and checks that package in a second clean Arch container; and
6. publishes the source archive, package, installer, `PKGBUILD`, and `SHA256SUMS` as a
   GitHub Release; and
7. leaves signing to the offline-key promotion step in
   [`PROMOTION.md`](PROMOTION.md), which signs those exact workflow artifacts.

`packaging/arch/PKGBUILD` deliberately points at the immutable release asset.
The source archive excludes that one recipe, avoiding a circular checksum. The
published recipe remains a separate release asset and pins the archive exactly.

Local development builds are separate:

```bash
./packaging/arch/build-local.sh
sudo pacman -U packaging/arch/paddock-0.1.7-25-x86_64.pkg.tar.zst
```

The local builder creates a temporary recipe at revision 25 and never rewrites
the release `PKGBUILD`.

## Publishing an application release

Only after `main` is green and the release source checksum is current:

```bash
git tag -a v<version> -m "Paddock <version>"
git push origin v<version>
```

Never move a published tag. A failed workflow publishes nothing; fix the
release inputs, bump the version, and create a new tag rather than replacing an
existing public release.

`SHA256SUMS` detects corruption but does not establish maintainer identity.
The release becomes signed only after both detached signatures have been
published by the manual promotion command. No private signing key is stored in
GitHub.
