# Release promotion checklist

Implements [ADR 0009](../docs/adr/0009-release-signing.md). Every step is
manual and run by the maintainer on a trusted workstation. No signing key is
stored in CI, and no workflow may create a release.

Local builds (`./release/php/build.sh <minor>`) are for testing only. Only the
attested CI build is ever published: a local archive carries no provenance a
third party can verify.

## 1. Build the candidate in CI

```bash
gh workflow run runtime-release.yml --repo lukeska/paddock --ref main \
  --field runtime=8.5
```

Uncached, a single runtime takes roughly 30 minutes; `runtime=all` builds the
two sequentially in one job, so budget about an hour.

Two failure modes seen in practice, both environmental:

- The apt step can stall on an Ubuntu mirror. It is now bounded to 10 minutes,
  so it fails fast; re-dispatch.
- The builder resolves dependency versions through the GitHub API. The step
  passes `GITHUB_TOKEN` for this reason: unauthenticated, `runtime=all`
  exhausted the per-IP quota partway through the second runtime and failed
  with `curl (22) 403`. If 403s reappear, check the token is still being
  passed before suspecting the mirrors.

## 2. Verify the candidate before it is published

```bash
gh run download RUN_ID --repo lukeska/paddock \
  --name paddock-php-<minor>-x86_64 --dir CANDIDATE
cd CANDIDATE
sha256sum --check paddock-php-*.tar.gz.sha256
gh attestation verify paddock-php-<version>-linux-x86_64.tar.gz \
  --repo lukeska/paddock --format json > attestation.json
```

`gh attestation verify` can exit 0 while printing nothing. Do not read a
silent success as verification. Assert on the JSON:

```bash
python3 - <<'PY'
import json
a = json.load(open("attestation.json"))[0]
cert = a["verificationResult"]["signature"]["certificate"]
subject = a["verificationResult"]["statement"]["subject"][0]
print(subject["name"], subject["digest"]["sha256"])
print(cert["sourceRepositoryDigest"], cert["runnerEnvironment"], cert["buildSignerURI"])
PY
```

Confirm the digest matches the archive on disk, the commit is the one you
intend to publish from, and `runnerEnvironment` is `github-hosted`.

Then confirm the runtime itself:

```bash
tar -xzf paddock-php-<version>-linux-x86_64.tar.gz -C x
x/runtime/bin/php -n -v          # CLI version
x/runtime/bin/php-fpm -n -v      # FPM version, must agree
```

Every Laravel baseline extension must be present, `intl` included.

The candidate's `artifacts.local.json` contains `file://` URLs by design. It
is build output, never a release input, and must not be copied into
`resources/artifacts.json`.

## 3. Publish the runtime release

Create an immutable tag `php-YYYY.MM.DD` and attach the archive, its
`.sha256`, the SPDX inventory, the compatibility record, and the build log.

Never move or delete a published runtime tag. The packaged index pins URLs at
that tag, so a moved tag breaks checksum verification for everyone already
installed.

## 4. Update the packaged index

Edit `resources/artifacts.json` by hand:

- URLs point at the release just published;
- each `sha256` is copied from the verified `.sha256` file, never from
  `artifacts.local.json`;
- re-download each published URL and confirm the hash matches the file.

```bash
PYTHONPATH=src python -m unittest tests.test_packaged_artifacts -v
```

That guard rejects non-HTTPS URLs, duplicate entries for a minor and
architecture, a URL naming a different archive than it pins, and a moving tag.

Commit the index change on its own, citing the attestation run ID.

## 5. Build and sign the package

```bash
./packaging/arch/build-local.sh
gpg --detach-sign --armor packaging/arch/paddock-<version>-<rel>-x86_64.pkg.tar.zst
```

Sign the AUR source archive the same way and keep the fingerprint listed in
`validpgpkeys`. Sign the repository database only if a custom pacman
repository is operated. Per ADR 0009, nothing else is signed: runtime archives
and the index are covered by the package signature and Sigstore attestation.

## 6. Record the release

Release notes state the PHP patch versions, extension changes, the builder
version and SHA-256, and the signing fingerprint. Retain every runtime release
referenced by any shipped index, plus the two most recent.

## Rollback

Reinstall the retained previous package with `pacman -U`. Because the index
ships inside the package, this rolls the runtime index back coherently; there
is no separate index rollback.

## Never

- Publish a locally built runtime archive.
- Grant `contents: write` to the runtime-candidate workflow.
- Store private key material in GitHub secrets.
- Copy `artifacts.local.json` over `resources/artifacts.json`.
