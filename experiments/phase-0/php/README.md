# Experiment 0.3: PHP Runtime Distribution

## Status

- State: passed for Phase 0 strategy selection; release engineering remains
- Date started: 2026-08-15
- System packages installed: `cmake`, `gperf`, and `re2c` through Omarchy
- Temporary downloads: public runtimes and pinned/nightly builders under `/tmp`
- Current gate: none for strategy selection; production supply-chain design is
  tracked in ADR 0004

The public runtime proof succeeded operationally but failed the strict extension
policy. Paddock-owned PHP 8.4.23 and 8.5.8 builds now pass the strict runtime
probe simultaneously and each loads a separately built Xdebug module.

## Candidates evaluated

### Arch official packages

Arch currently packages PHP 8.5 and PHP-FPM 8.5 in the official Extra
repository. This is a trusted, signed, low-maintenance source for the current PHP
line, but it does not provide simultaneous older PHP lines and therefore cannot
implement per-site isolation by itself.

Source: [Arch PHP-FPM package](https://archlinux.org/packages/extra/x86_64/php-fpm/)

### Versioned AUR packages

The AUR provides versioned package bases such as `php83`, `php84`, and `php85`,
including CLI, FPM, and extension subpackages. They can coexist by name, but they
have a large build/subpackage surface, rely on user-reviewed AUR recipes, consume
system-wide package state, and have already had reports of confusing base versus
CLI package composition.

Sources: [PHP 8.3 AUR package base](https://aur.archlinux.org/packages/php83-gmp),
[PHP 8.4 AUR package base](https://aur.archlinux.org/packages/php84)

This remains a fallback for advanced users, not the preferred managed-runtime
model.

### Third-party public StaticPHP artifacts

StaticPHP supports PHP 8.1 through 8.5 and produces independent CLI and FPM
binaries for Linux x86_64 and aarch64. Its default Linux target is fully static
musl, while an optional glibc target can load compatible dynamic extensions.

Sources: [StaticPHP SAPI reference](https://static-php.dev/en/guide/sapi-reference.html),
[supported extensions](https://static-php.dev/en/guide/extensions.html)

Public `common` artifacts were staged for:

| Runtime | CLI | FPM |
| --- | --- | --- |
| PHP 8.4.23 | passed | passed |
| PHP 8.5.8 | passed | passed |

All four binaries were statically linked, independent executables. The archive
hashes observed during this experiment were:

```text
1aeed5bc7967977ca5b1da7163acd91bf9ba3ac56037045d4e91ee2ff2712bb7  php-8.4.23-cli-linux-x86_64.tar.gz
baa5ddcf2f9aabff78848cb6851bb104a4c0873347b8ee65eb9bcf4760a4d31a  php-8.4.23-fpm-linux-x86_64.tar.gz
517a18e0f0874de35669fe134eb3c52508e128a6d1da5bf890760cb800ada410  php-8.5.8-cli-linux-x86_64.tar.gz
836412ac76432e9f8e49a88c440c381acb2d262cc6dab4ba0a80468ab568a239  php-8.5.8-fpm-linux-x86_64.tar.gz
```

These are locally observed hashes, not an upstream signature or attestation, and
must not be treated as an approved supply-chain mechanism.

### Herd Lite PHP

The foreign Herd Lite PHP 8.5.0 binary was inspected without modification. It is
fully static and includes a broad Laravel-oriented extension set, including
`intl`, Imagick, MongoDB, MySQL, PostgreSQL, Redis, and XSL. It does not include
an FPM binary, has no Paddock ownership boundary, and therefore cannot provide
the managed HTTP runtime.

It proves that a rich relocatable PHP CLI works on this Omarchy machine but is
not a distribution source Paddock may adopt.

## Tests completed

### CLI and fixture behavior

Both public StaticPHP CLI binaries passed:

- Version execution.
- Composer 2.8.12 execution under the selected binary.
- The two-app fixture smoke test.
- Static file, health, nested route, runtime, HTTP 500, and log assertions.

### FPM behavior

Both public FPM binaries passed:

- CLI/FPM patch-version agreement.
- Configuration validation.
- Simultaneous FPM masters.
- Separate private Unix sockets.
- Separate PID and error-log paths.
- Bounded shutdown and complete temporary cleanup.

### Required extensions

The strict baseline is:

```text
curl dom fileinfo filter intl mbstring openssl pdo session tokenizer xml zip
```

Both public StaticPHP artifacts contained every baseline extension except
`intl`. They also contained useful bundled support for MySQL, PostgreSQL,
SQLite, Redis, GD, GMP, SOAP, and sockets.

Because fully static binaries cannot load later `.so` extensions, missing
`intl` is a build-time failure rather than an installation-time option. The
public artifacts are rejected for Paddock despite passing operational tests.

## Builder check

StaticPHP 3.0.0-alpha1 was downloaded to the temporary staging directory and its
environment doctor was run. It recognized Omarchy x86_64 as supported and found
three missing host build tools:

- `cmake`
- `gperf`
- `re2c`

The user installed all three explicitly through Omarchy. StaticPHP then
downloaded builder-local `pkg-config` and Zig toolchains into `/tmp`.

The v3 alpha build was rejected after Zig's linker crashed while linking cURL's
standalone executable. The builder re-extracted cURL on every retry, so a local
CMake cache/source workaround was not reproducible.

StaticPHP 2.8.5 was then downloaded from its signed GitHub release and its asset
digest was verified as:

```text
523ba4279c54c7a377156c0dd3a36adf92ee64b01e9a7f5e9e2ec084b8e458e5
```

Unlike the alpha, v2.8.5 disables the cURL executable and completed the glibc
build. This exact builder is the current feasibility candidate, not yet a final
production pin.

### Custom PHP 8.4.23 and 8.5.8 result

StaticPHP 2.8.5 produced independent glibc-compatible PIE executables targeting
glibc 2.17 for both versions:

```text
buildroot/bin/php
buildroot/bin/php-fpm
```

Observed results:

- PHP CLI and FPM report 8.4.23 and 8.5.8 respectively.
- Composer 2.8.12 runs under the built CLI.
- All strict baseline extensions pass; `intl` reports ICU 77.1.
- The binaries dynamically depend only on the glibc runtime family; bundled
  third-party libraries are linked into the executables.
- The two version-specific FPM masters run simultaneously on isolated Unix
  sockets.

The optional-extension stage produced a distinct `modules/xdebug.so` for each
version. Xdebug 3.5.3 is not loaded by default and loads successfully via
`zend_extension` when requested. The observed compatibility tuples are:

| PHP | PHP API | Zend extension API | Mode |
| --- | --- | --- | --- |
| 8.4.23 | 20240924 | 420240924 | x86_64, NTS, non-debug, glibc |
| 8.5.8 | 20250925 | 420250925 | x86_64, NTS, non-debug, glibc |

Cross-loading was rejected safely in both directions. PHP 8.5 explicitly
reported that the PHP 8.4 module requires Zend API 420240924 rather than
420250925. PHP 8.4 rejected the PHP 8.5 module on an unresolved ABI symbol. In
both cases `extension_loaded("xdebug")` remained false.

This proves the proposed three-tier extension model and the need for strict
version/ABI validation before changing `conf.d` activation state.

### Relocatable configuration result

`runtime-command.sh` establishes the runtime configuration contract for both
CLI and FPM:

- Resolve the selected runtime root at invocation time.
- Set `PHPRC` to `<runtime>/etc/php.ini`.
- Set `PHP_INI_SCAN_DIR` to `<runtime>/etc/conf.d`.
- Set `extension_dir` to `<runtime>/modules` before parsing optional-extension
  directives.
- Execute only a `php` or `php-fpm` binary inside that runtime root.

Both PHP versions loaded their version-local primary INI and scan directory.
With `20-xdebug.ini.disabled`, Xdebug remained unloaded. Renaming it to
`20-xdebug.ini` enabled Xdebug in both CLI and FPM without modifying `php.ini`.

The PHP 8.4 runtime directory was then moved to a different absolute path. The
same launcher and configuration probe passed at the new location, after which
the runtime was restored. This proves the runtime itself does not depend on its
staging path. PHP still reports its compiled `/usr/local/etc/php` default as
metadata, but the launcher overrides that lookup completely.

### Laravel 13 platform result

A clean `laravel/laravel` 13.9.0 application was installed with PHP 8.4.23. Its
resolved framework version was Laravel 13.25.0 with 109 locked packages.
Installation completed package discovery, key generation, SQLite creation, and
the initial migrations.

Under both PHP 8.4.23 and 8.5.8:

- `composer check-platform-reqs --lock` passed every locked requirement.
- `artisan about` booted the application and reported the selected PHP version.
- The default PHPUnit suite passed 2 tests and 2 assertions.

### Independence and reproducibility results

The complete PHP 8.4 staging directory was made unavailable temporarily. PHP
8.5 still passed its configuration probe and the Laravel test suite. Restoring
8.4 required no changes to 8.5, proving the version roots are operationally
independent.

PHP 8.4 was rebuilt from the same cached source lock and build recipe. The
functional checks passed again, but the binary hashes changed:

| Artifact | First SHA-256 | Rebuild SHA-256 |
| --- | --- | --- |
| `bin/php` | `4b8211606b2bff355ff44139751e648c5706fa08b939d00182b05f3571b1d0b1` | `f6f2cecc4bf2a9134afcc51b07df314a4ade09e153a89cb10bb915b82db0e8c6` |
| `bin/php-fpm` | `7f7daf4b3f5aa4d58b0a415f934071f7f2f80f62035e146870966726e19d4671` | `c7c2d142b80aee34fca62011237cbe44f9436a9b2c68bbf74b4d3d5198f8c950` |

PHP embeds its build date (`10:34:54` versus `11:00:40`), so byte-for-byte
reproducibility is not currently achieved. Absolute workspace paths also appear
in configure metadata. Release CI must control timestamps (for example through
`SOURCE_DATE_EPOCH` where supported), normalize build paths, and investigate
remaining differences before claiming reproducible artifacts.

Rebuilding the base runtime also clears assembled optional modules. Therefore a
release must be assembled in this order: base runtime, matching optional module
packages, complete validation, then atomic publication. Updating files in place
is not acceptable.

## Probe tooling

`probe-runtimes.sh` validates two or more runtime roots. For every runtime it:

- Requires matching `bin/php` and `bin/php-fpm` executables.
- Requires exact CLI/FPM version agreement.
- Enforces the Laravel extension baseline.
- Optionally executes a supplied Composer PHAR.
- Validates generated FPM configuration.
- Starts all FPM masters simultaneously on private Unix sockets.
- Verifies they remain alive and cleans them up on every exit path.

Strict usage:

```bash
COMPOSER_PATH=/absolute/path/to/composer \
  ./experiments/phase-0/php/probe-runtimes.sh \
  /path/to/php-8.4 /path/to/php-8.5
```

`REQUIRED_EXTENSIONS` can be overridden only to diagnose public or incomplete
artifacts. It must never be overridden for acceptance.

## Provisional comparison

Scores use the Phase 0 scale of 1 (poor) to 5 (excellent).

| Criterion | Official Arch | Versioned AUR | Public StaticPHP | Paddock StaticPHP builds |
| --- | ---: | ---: | ---: | ---: |
| Security/provenance | 5 | 2 | 2 | 4 target |
| Multiple versions | 1 | 5 | 5 | 5 |
| CLI and FPM | 5 | 5 | 5 | 5 |
| Required extensions | 4 | 5 | 2 observed | 5 target |
| Isolation from system PHP | 1 | 2 | 5 | 5 |
| Install/update speed | 5 | 1 | 5 | 5 target |
| Optional dynamic extensions | 5 | 5 | 1 | 3 if glibc target |
| Maintenance burden | 5 | 2 | 3 | 2 |
| Reproducibility/control | 4 | 3 | 2 | 5 target |
| Clean removal | 2 | 2 | 5 | 5 |

The target scores for Paddock-owned builds remain hypotheses until the custom
build and release pipeline are demonstrated.

## Updated provisional direction

Use Paddock-built, glibc-compatible relocatable PHP distributions generated
from pinned StaticPHP source, with:

- Separate CLI and FPM executables for each patch version.
- A broad, curated extension baseline comparable to Laravel Herd and Laravel
  Forge environments.
- Dynamic `.so` extension loading.
- Version-matched `phpize`, `php-config`, headers, and build metadata.
- Paddock-built optional extension packages for common additions such as
  Xdebug and PCOV.
- Version-specific `conf.d` activation rather than rewriting the primary
  `php.ini`.
- x86_64 first, followed by native aarch64 CI.
- Checksums, SBOM, build logs, and artifact attestations per release.
- Installation under Paddock-owned version directories.
- Atomic activation and rollback without touching system PHP.
- Compatibility validation using PHP API, Zend API, architecture, libc, and
  thread mode before any shared extension is enabled.

This is provisional, not accepted until tested. Fully static musl is no longer a
candidate for the primary product runtime because it prevents users from loading
additional extensions. It remains useful only for disposable experiments or
special-purpose tools.

This direction mirrors Herd's model: bundle a large common extension set, ship
selected version-specific extensions such as Xdebug, and permit compatible
user-supplied modules through per-version configuration. See Herd's
[PHP extension documentation](https://herd.laravel.com/docs/macos/technology/php-extensions)
and [Xdebug packaging](https://herd.laravel.com/docs/macos/debugging/xdebug).

## Production follow-up work

1. Pin and review the final StaticPHP source commit used by CI, not only the
   verified 2.8.5 release binary.
2. Add checksums, an SBOM, build logs, signing, and attestations to the release
   pipeline.
3. Eliminate or formally account for the observed build nondeterminism.
4. Convert the extension tiers and compatibility tuple into a signed package
   index format.
5. Accept ADR 0004 after those release-engineering conditions are designed and
   validated.

## Reproduction helpers

`build-runtime.sh` builds the base CLI/FPM runtime from `craft.yml.in`.
`build-optional-extension.sh` then downloads and builds an ABI-matched shared
module against that runtime. Keeping these stages separate models optional
extensions accurately and avoids compiling them into the base executable.
`runtime-command.sh` applies version-local configuration to either SAPI, while
`probe-runtime-config.sh` verifies relocation and optional-extension toggling.
