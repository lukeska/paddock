# ADR 0009: Release Signing and Promotion

- Status: accepted for implementation
- Date: 2026-08-19

## Context

Paddock ships an Arch package containing the CLI, root helpers, and the
artifact index. The index is installed to `/usr/share/paddock/artifacts.json`
and is never fetched at runtime; `RuntimeInstaller` downloads the URL it names
and activates the archive only after the pinned SHA-256 matches. Runtime trust
therefore already chains from the package:

```text
package signature
  -> /usr/share/paddock/artifacts.json (pinned sha256 per runtime)
    -> runtime archive verified on download, rejected on mismatch
```

The runtime-candidate workflow builds runtimes on a GitHub-hosted runner and
produces SLSA provenance through Sigstore keyless signing, binding each
archive to a commit, workflow identity, and runner. It holds `contents: read`
and `attestations: write` and deliberately cannot publish a release.

Attestation and distribution signing answer different questions. Attestation
answers "how was this built"; a package signature answers "who vouches for
this". Both are needed, but they are not substitutes and should not be
duplicated onto the same subjects.

Paddock has a single maintainer. Controls that assume independent review must
be described accurately rather than aspirationally.

## Decision

### Key ownership

Generate an Ed25519 primary key offline and keep it off any networked machine,
together with a pre-generated revocation certificate stored separately. Use a
signing subkey with a 1-2 year expiry that is extended rather than replaced.
Publish the fingerprint in the repository README and in release notes.

No private key material is stored in GitHub. Releases are signed locally by
the maintainer at promotion time. A hardware token holding a non-exportable
subkey is the intended future upgrade and requires only a key migration, not a
process change.

CI-side signing is explicitly rejected at this stage. The candidate workflow
was built read-only so that it cannot publish; exporting a signing key into
repository secrets would remove that boundary in exchange for automating a
step that runs at most monthly and already requires a human to review the
candidate. Revisit only when manual promotion is demonstrably the bottleneck.

### Signed subjects

Sign exactly what pacman verifies:

- the Arch package (`.pkg.tar.zst`), detached signature;
- the source archive consumed by the AUR `PKGBUILD`, detached signature, with
  the key listed in `validpgpkeys`;
- the repository database, if and only if a custom pacman repository is
  operated.

Do not separately sign runtime archives, `resources/artifacts.json`, SBOMs,
provenance, compatibility records, or build logs. Runtime archives are
hash-pinned inside the signed package and already carry Sigstore attestation;
the index ships inside the signed package and is never fetched. Signing these
would add subjects to rotate and revoke without moving the security boundary.
Revisit if the index ever becomes network-fetched, which would change the
trust chain above.

### Secrets, environments, and permissions

No GitHub secrets hold key material. No protected environment is created while
no workflow needs write scope.

The candidate workflow must never gain `contents: write`. If publication is
ever automated it goes in a separate workflow, so the candidate builder keeps
its read-only guarantee. A required-reviewer gate with a single maintainer is
a deliberate pause, not independent review, and must be documented as such.

### Naming, retention, rollback, verification

- Two tag namespaces: `v<semver>` for the CLI and package, `php-YYYY.MM.DD`
  for runtime bundles. They move on unrelated cadences.
- Published runtime tags are immutable. Never move or delete one: the packaged
  index pins URLs at that tag, so a moved tag silently breaks checksum
  verification for every installed user.
- Retain every runtime release referenced by any shipped index, plus the two
  most recent regardless.
- Release notes state PHP patch versions, extension changes, the builder
  version and its SHA-256, and the signing fingerprint used.
- Rollback is `pacman -U` of the retained previous package. Because the index
  is packaged, a package rollback also rolls the runtime index back
  coherently; no separate index rollback exists.
- The README documents verification: import by fingerprint, verify the package
  signature, and `gh attestation verify` for build provenance.

### Promotion of the artifact index

Published runtimes are the attested CI build. A locally built archive is for
testing only and must never be published, because it carries no verifiable
provenance.

Promotion is manual and ordered:

1. Build the candidate in CI; verify its checksum and attestation.
2. Publish the runtime under an immutable `php-YYYY.MM.DD` tag.
3. Update `resources/artifacts.json` to the release URLs, taking each SHA-256
   from the verified `.sha256` file, never from the candidate's
   `artifacts.local.json`.
4. Re-download each published URL and confirm the hash matches.
5. Commit the index change alone, citing the attestation run ID.
6. Only then bump the package version and build the release package.

## Consequences

- Publication requires the maintainer to be present. This is accepted.
- A compromise of the workstation compromises signing. The offline primary and
  stored revocation certificate bound the damage to subkey revocation and
  re-signing rather than identity loss.
- Loss of the primary key without the revocation certificate is unrecoverable,
  which is why the certificate is generated at key creation and stored apart.
- Users verifying provenance need `gh` or a Sigstore client; users verifying
  distribution need only pacman and the published fingerprint.

## Guardrails already implemented

`tests/test_packaged_artifacts.py` validates the shipped index through the
production manifest parser and additionally requires HTTPS-only URLs, no
duplicate entry per minor and architecture, each URL naming the archive it
pins, and an immutable release tag. The parser rejects an empty URL but not a
scheme, so a `file://` URL from a local build previously passed every check.
This enforces both the "published runtimes are the attested CI build" rule and
the immutable-tag retention rule.

## Open items

The signing key does not exist yet. Before the first signed release the
maintainer generates it offline, stores the revocation certificate separately,
and publishes the fingerprint in the README, replacing the placeholder. No key
has been created as part of this decision.
