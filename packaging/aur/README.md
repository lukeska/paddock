# AUR recipe

This directory contains the submission-ready recipe for the latest signed
Paddock release. It deliberately trails development metadata: update it only
after the corresponding GitHub release exists and both detached signatures
have been published and independently verified.

The AUR recipe differs from `packaging/arch/PKGBUILD` in one important way. It
downloads the source archive and its detached signature, pins the full primary
fingerprint in `validpgpkeys`, and uses `SKIP` only for the signature itself.
The Arch recipe used by the release workflow cannot require a signature that
does not exist until after that workflow has built and published the archive.

Before submitting an update:

```bash
cd packaging/aur
makepkg --verifysource
makepkg --printsrcinfo > .SRCINFO
makepkg --cleanbuild
```

Commit `PKGBUILD`, `.SRCINFO`, and `paddock.install` to the AUR repository only
after all three commands pass. Never replace a signature or reuse a version.
