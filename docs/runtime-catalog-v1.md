# Runtime catalog v1: publication contract

This is the format for independently refreshed runtime metadata. It is not
enabled in Paddock yet; Unit 2 of [the implementation plan](runtime-updates-plan.md)
adds signature verification and fetching, and Unit 6 adds promotion tooling.

## Distribution

The stable, mutable *discovery* URLs will be served by GitHub Pages from the
repository's dedicated `gh-pages` branch (root publishing source, with
`.nojekyll`):

| Runtime | Catalog | Detached signature |
| --- | --- | --- |
| PHP | `https://lukeska.github.io/paddock/runtime-catalogs/php.json` | same URL with `.sig` appended |
| Node | `https://lukeska.github.io/paddock/runtime-catalogs/node.json` | same URL with `.sig` appended |

GitHub Pages must be explicitly configured before enabling client refresh;
these URLs are a contract, **not currently advertised as live**. The
`gh-pages` branch is not a package source and carries no signing key. A Pages
deployment is not an authenticity signal: clients trust only a detached
signature verified against the public key shipped in the Paddock package.

Each promotion also publishes an immutable archived copy of the four files at
`runtime-catalogs/archive/<kind>/<revision>.json[.sig]` on the same branch.
Never replace or delete an archived revision. Publish the archive first, then
the stable discovery pair. Because two HTTP requests cannot be atomic, a
client may briefly see mismatched catalog/signature bytes; verification must
fail without replacing its last trusted copy, and the next refresh may retry.
GitHub Pages deployments can lag branch pushes; do not treat a successful push
as confirmation that the stable URL is serving the new revision.

## Signed JSON

The detached OpenPGP signature covers the exact bytes of one UTF-8 JSON file.
Whitespace and key order are not normalized by the client. The signature is
made locally with the Paddock release signing subkey, not by CI. The package
contains the public key and pins the full primary fingerprint
`AB3611DC044DE36844055E9AC1A41BDC59DCEA60`. Use a dedicated verification
keyring; neither the user's keyring nor a network-fetched key extends trust.

Each catalog is an object with exactly these fields:

```json
{
  "schema_version": 1,
  "revision": 1,
  "artifacts": []
}
```

`revision` is a positive integer, strictly increasing for each runtime kind;
PHP and Node have independent sequences. Every published revision contains at
least one artifact. Once accepted, a client records the highest revision and
the SHA-256 of the signed catalog bytes in durable state. Lower revisions are
rejected; an equal revision is accepted only if its bytes have the recorded
hash. Signing a different document at the same revision is forbidden.

PHP artifacts use the existing packaged fields `php`, `minor`,
`architecture`, `url`, `sha256`; Node uses `node`, `major`, `architecture`,
`url`, `sha256`. The patch version must match its minor/major. At most one
artifact per minor/major and architecture appears in a catalog. SHA-256 is 64
lowercase hexadecimal characters. Supported architecture names are `x86_64`
and `aarch64`. PHP artifacts are currently published only for `x86_64`; Node
supports both. The schema permits either architecture only when there is a
real, verified artifact for it. Omission means unavailable, not a request to
fall back to an artifact for another CPU.

PHP artifact URLs must be exact HTTPS GitHub release downloads from
`lukeska/paddock` under a versioned `php-YYYY.MM.DD` tag, with the expected
version/architecture filename. Node URLs must be exact HTTPS
`nodejs.org/download/release/v<patch>/node-v<patch>-linux-<arch>.tar.xz`
paths. URLs with credentials, query strings, fragments, mutable `latest`
aliases, or alternate hosts are rejected. The archive is separately checked
against `sha256` before extraction; a catalog signature does not replace the
archive checksum.

The bundled package catalogs remain bootstrap and offline fallback. A local
catalog override is explicitly user-owned and takes precedence; remote
refresh never overwrites it. The precise cache/state locations and fallback
status behavior are specified in Unit 2.

## Rotation, revocation, and recovery

The current key may be extended through the offline primary as in ADR 0009.
If the signing subkey must be replaced, first ship a new Paddock package
containing the new public certificate and verifier policy; then sign catalogs
with the new subkey. Clients without that package continue using their last
trusted catalog or bundled fallback. Never fetch a replacement trust key from
the catalog endpoint. Compromise requires revocation and a package update;
do not present catalog refresh as safe until affected clients have a new trust
anchor. A bad but validly signed revision can be corrected only by publishing
a higher revision; never overwrite or move the old archived revision. The
runtime archives referenced by any published catalog revision must be kept.
