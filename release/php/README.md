# PHP runtime release pipeline

`build.sh` uses the pinned StaticPHP 2.8.5 builder and the accepted Phase 0
craft manifest to build PHP 8.4.23 and 8.5.8 for glibc 2.17+ on x86_64.

```bash
./release/php/build.sh all
```

The build validates CLI/FPM agreement, the Laravel baseline extensions,
version-local configuration, Xdebug compatibility, simultaneous FPM startup,
and optional Composer execution when `COMPOSER_PATH` is supplied. Assembly is
stable (sorted archive, normalized timestamps and ownership), but PHP binaries
are not claimed byte-reproducible until the embedded build timestamp/path issue
recorded in Phase 0 is fixed.

Each artifact receives:

- SHA-256 checksum file;
- file-level SPDX 2.3 inventory;
- PHP/Zend ABI compatibility tuple;
- unsigned in-toto/SLSA provenance statement;
- complete build log.

Runtime archives contain the PHP CLI and FPM executables, optional prebuilt
modules, runtime configuration, licenses, and build manifests. Toolchain files,
headers, static libraries, debug symbols, and build-only utilities remain in
the release workspace. A future development-kit artifact can publish those
separately without making every runtime installation carry them.

The local index uses `file://` URLs and must never replace the packaged index.
For publication, rerun `index.py` with `--base-url` and sign every artifact,
metadata file, index, source archive, package, and repository database.

## GitHub release candidates

The manually dispatched `Runtime release candidate` workflow builds either
supported minor or the full matrix on Ubuntu 22.04, repeats all runtime probes,
checks every archive digest and JSON document, and creates a GitHub build
attestation for each runtime archive. The complete candidate bundle is retained
as a workflow artifact for 14 days.

The workflow deliberately cannot create a release. Promotion remains blocked
until the signing identity, protected GitHub environment, and public release
index update have been configured and reviewed.
