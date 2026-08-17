# ADR 0004: PHP Runtime Distribution

- Status: proposed; custom runtime/extension validation passed, packaging pending
- Date: 2026-08-15
- Experiment: [PHP runtime distribution](../../experiments/phase-0/php/README.md)

## Context

Paddock needs simultaneous PHP CLI and FPM versions, per-project selection,
predictable extensions, fast updates, and removal that does not modify system
PHP. Arch's official repositories intentionally center the current PHP line,
while versioned AUR packages and third-party static artifacts have different
maintenance and trust tradeoffs.

## Proposed decision

Publish Paddock-owned, glibc-compatible relocatable PHP CLI/FPM pairs built
from pinned StaticPHP source and a reviewed extension manifest. The runtime must
support compatible shared `.so` extensions. Install each patch release in a
versioned Paddock-owned directory and activate versions atomically.

Use three extension tiers:

1. Bundled extensions compiled into or shipped with every runtime.
2. Paddock-managed optional extensions built, signed, and indexed for each
   supported compatibility tuple.
3. User-managed extensions built with version-matched tools and enabled only
   after compatibility validation.

Store activation in version-specific `conf.d` files. Do not rewrite the primary
`php.ini` merely to toggle an extension.

Do not adopt the user's system PHP, AUR runtime directories, Herd Lite files, or
unsigned third-party artifacts as Paddock-owned state.

## Conditions before acceptance

- PHP 8.4 and 8.5 custom builds pass the strict runtime probe.
- `intl` and every baseline Laravel extension are present.
- Composer and a minimal Laravel application pass on both versions.
- Supply-chain metadata includes hashes, an SBOM, build logs, and attestations.
- Both versions load a matching shared test extension and reject an incompatible
  one safely.
- Version-matched `phpize`, `php-config`, headers, and build metadata are
  available for PIE/PECL or manual builds.
- Xdebug or PCOV can be installed, enabled, disabled, and removed independently
  for each PHP version.
- Independent update, rollback, and removal are demonstrated.

## Consequences if accepted

- Runtime installation is fast and does not compile on the user's machine.
- Paddock owns security rebuild cadence and artifact hosting.
- The release matrix must cover every supported PHP/architecture combination.
- Glibc-compatible builds allow shared extensions but add ABI testing and
  dependency constraints.
- Paddock must publish and validate the complete compatibility tuple: PHP API,
  Zend API, architecture, libc baseline, and thread mode.
- Fully static musl builds are excluded from the primary runtime because every
  extension would have to be present at build time.

## Alternatives retained

- Official Arch PHP may be supported as an unmanaged escape hatch for the
  current version, but cannot power the managed multi-version promise.
- Versioned AUR packages may be documented for contributors, but should not be
  silently installed as the default runtime strategy.
- Direct StaticPHP public artifacts remain useful for prototypes, not releases,
  because the tested common builds omitted `intl` and lacked an accepted
  Paddock provenance chain.
