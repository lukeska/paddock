# Release signing key ceremony

Paddock's release identity is created once, on a trusted machine disconnected
from every network. This document is a checklist, not an automated script: the
primary key, its passphrase, and the revocation certificate must never enter
this repository, GitHub Actions, or an online backup.

## Create the offline primary

Install a current GnuPG release from the trusted operating system, disconnect
the machine, and create a certification-only Ed25519 primary key:

```bash
gpg --quick-generate-key \
  "Paddock Release Signing <20092363+lukeska@users.noreply.github.com>" \
  ed25519 cert 0
gpg --list-secret-keys --with-subkey-fingerprint --keyid-format long
```

Record and independently re-check the full 40-character primary fingerprint.
Use a strong unique passphrase. This GitHub-provided `noreply` address is a
deliberate public identity and does not disclose the maintainer's private
email address.

Create a signing subkey with a two-year expiry:

```bash
gpg --quick-add-key <PRIMARY-FINGERPRINT> ed25519 sign 2y
```

Generate the revocation certificate while the primary is available:

```bash
gpg --output paddock-release-revocation.asc \
  --gen-revoke <PRIMARY-FINGERPRINT>
```

Store the primary-key backup and revocation certificate on separate encrypted
offline media. Test both backups on the disconnected machine before erasing
any working copy.

Create the encrypted primary-key backup while still offline:

```bash
gpg --armor --export-secret-keys \
  AB3611DC044DE36844055E9AC1A41BDC59DCEA60 \
  > paddock-release-primary-backup.asc
```

## Prepare the signing workstation

Export only the public key and the secret signing subkey. Never export the
secret primary key to the online workstation:

```bash
gpg --armor --export AB3611DC044DE36844055E9AC1A41BDC59DCEA60 \
  > paddock-release-public.asc
gpg --armor --export-secret-subkeys \
  AB3611DC044DE36844055E9AC1A41BDC59DCEA60 \
  > paddock-release-signing-subkeys.asc
```

Transfer these through trusted removable media, import them on the signing
workstation, and securely remove the transferred secret-subkey file after the
import. Confirm that `gpg --list-secret-keys` shows a stub (`#`) for the secret
primary and an available secret signing subkey.

Publish the public key and fingerprint through at least the GitHub repository
and release notes. The fingerprint must be copied from GnuPG output, never
typed from memory.

## Sign a release

Follow [`release/PROMOTION.md`](../release/PROMOTION.md). The promotion command
downloads and verifies the exact workflow-built artifacts before asking GnuPG
to sign them. First create and inspect signatures without publishing; then use
the explicit `--publish` form from a fresh directory.

Package signatures use the conventional binary `.sig` form understood by
pacman. Source signatures use the same detached format understood by
`makepkg`; the AUR recipe must list the signature immediately after its source,
use `SKIP` for the signature checksum, and pin the full primary fingerprint in
`validpgpkeys`.

## Rotate or revoke

Before the signing subkey expires, use the offline primary to extend it or add
a replacement signing subkey, then refresh the public key everywhere it is
published. If the signing workstation or subkey may be compromised, revoke
that subkey with the offline primary and publish the updated public key. Use
the separately stored revocation certificate only when the primary itself is
lost or compromised.
