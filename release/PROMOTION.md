# Release promotion checklist

Implements [ADR 0009](../docs/adr/0009-release-signing.md). Every step is
manual and run by the maintainer on a trusted workstation. No signing key is
stored in CI, and the runtime-candidate workflow may not create a release.
Application releases use the separate tag workflow documented in
[`release/README.md`](README.md); it has no signing key and cannot publish until
its package passes a clean-container installation.

Local builds (`./release/php/build.sh <minor>`) are for testing only. Only the
attested CI build is ever published: a local archive carries no provenance a
third party can verify.

## 1. Build the candidate in CI

```bash
gh workflow run runtime-release.yml --repo lukeska/paddock --ref main \
  --field runtime=8.5
```

Uncached, a single runtime takes roughly 30 minutes; `runtime=all` builds all
six PHP 8 minors sequentially in one job, so budget about three hours.

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

## 5. Sign the application release

```bash
./release/sign-application-release.sh v<version> <PRIMARY-FINGERPRINT>
```

This downloads the already-tested source archive and Arch package from the
GitHub release, verifies every entry in `SHA256SUMS`, refuses to replace an
existing release signature, signs both distribution subjects, and verifies
the resulting signatures locally. It deliberately does not rebuild anything.
Review the two `.sig` files and then publish them explicitly:

```bash
./release/sign-application-release.sh --publish \
  v<version> <PRIMARY-FINGERPRINT> /path/to/a/fresh/directory
```

The publish form performs the same download, checksum, signature, and local
verification gates before uploading. It never uses `--clobber`; a release that
already has a `.sig` asset must be investigated instead of silently replaced.

The source signature is the one an AUR recipe will list beside the source
archive, with the full primary fingerprint in `validpgpkeys`. The package
signature is the one pacman verifies. Sign a repository database only if a
custom pacman repository is operated. The *packaged* runtime index needs no
separate signature; independently refreshed catalogs have a separate manual
promotion process described below.

## Independent runtime-catalog promotion

The [catalog v1 contract](../docs/runtime-catalog-v1.md) selects a dedicated
`gh-pages` branch. `release/catalog.py` prepares canonical bytes, re-downloads
each archive, checks its hash and architecture coverage, verifies PHP's GitHub
Actions attestation (trusted workflow and hosted runner), and compares Node
archives with the official version's `SHASUMS256.txt` over HTTPS. It rejects
patch regression and changing a published patch's URL or hash. The tool never
signs or handles a private key; it verifies signatures against the packaged
public certificate and pinned primary fingerprint in an isolated keyring.

For an initial promotion, create a clean `gh-pages` checkout of
`github.com/lukeska/paddock`, configure Pages to publish from its root, and
include `.nojekyll`. For subsequent revisions, supply the current stable
catalog as `--current`. Do this once for PHP and once for Node; each has its
own revision sequence. The candidate is a reviewed packaged-format index
(for example, `resources/artifacts.json` or `resources/node-artifacts.json`).

```bash
python release/catalog.py prepare --kind php \
  --candidate resources/artifacts.json \
  --output /path/to/signing/php.json

# For subsequent revisions add:
# --current /path/to/current/php.json

gpg --local-user AB3611DC044DE36844055E9AC1A41BDC59DCEA60 \
  --detach-sign --output /path/to/signing/php.json.sig \
  /path/to/signing/php.json

python release/catalog.py verify --kind php \
  --catalog /path/to/signing/php.json \
  --signature /path/to/signing/php.json.sig

python release/catalog.py publish --kind php \
  --catalog /path/to/signing/php.json \
  --signature /path/to/signing/php.json.sig \
  --pages-dir /path/to/clean/gh-pages-checkout
```

Repeat with `--kind node`, its Node candidate, and its own output files. Review
the provenance lines from `prepare` and the canonical JSON before signing.
`publish` requires a clean, up-to-date `gh-pages` checkout with this
repository as `origin`; it refuses an existing archived revision. It pushes
the immutable archive first, then the stable discovery pair in a second
commit, and waits for GitHub Pages to serve the exact catalog and signature.
If Pages delivery times out after a push, inspect it before retrying: the
archive may already be published and must not be replaced. No private key or
signing operation belongs in CI.

Keep every referenced runtime archive indefinitely. Correct a bad signed
revision by publishing a higher revision, never by editing its archive or
moving a runtime tag. For key compromise, revoke and ship a new package trust
anchor before resuming remote refresh. The first real promotion still needs
the Omarchy VM acceptance checklist in the
[runtime update plan](../docs/runtime-updates-plan.md) before default
background refresh is enabled.

## 6. Record the release

Release notes state the PHP patch versions, extension changes, the builder
version and SHA-256, and the signing fingerprint. Retain every runtime release
referenced by any shipped index, plus the two most recent.

## Rollback

Reinstall the retained previous package with `pacman -U`. Its bundled index
rolls back with the package. After independent catalog refresh is enabled,
the user's highest accepted remote catalog revision remains in durable state;
package rollback cannot silently downgrade it. Runtime binary rollback is an
explicit `paddock php rollback VERSION` or `paddock node rollback VERSION`
command.

## Never

- Publish a locally built runtime archive.
- Grant `contents: write` to the runtime-candidate workflow.
- Store private key material in GitHub secrets.
- Copy `artifacts.local.json` over `resources/artifacts.json`.
