# Experiment 0.10: Project-Aware CLI PHP

## Status

- State: passed
- Date started: 2026-08-16
- Prototype implementation: Python standard library, used only to validate
  semantics before the production CLI language is selected

## Selection contract

1. Canonicalize the current working directory by resolving symlinks.
2. Walk from that directory toward `/` and find the nearest
   `.paddock.json` containing a PHP version.
3. Compare that candidate with canonical linked-site roots from the registry.
4. Select the candidate with the shortest ancestor distance; a local project
   declaration wins a tie at the same directory.
5. If no project candidate exists, use the configured default PHP version.
6. Resolve the selected version through the managed runtime registry. Project
   files may choose a version, but never an executable path.

The registry is a single generated JSON document containing the default version,
managed executable paths, Composer path, and linked site roots. Production
writes must be atomic; this experiment focuses on lookup and execution behavior.

## Execution contract

- `php` replaces the dispatcher process with the selected PHP executable.
- `composer` replaces it with selected PHP executing the configured Composer
  PHAR.
- The dispatcher does not modify PATH, shell startup files, aliases, or the
  system `php` command.
- The environment and child exit code pass through unchanged.
- A dispatcher-level `--` is removed once; every following argument is passed
  to PHP or Composer verbatim.
- Missing versions fail before execution with the version, project source, and
  a direct install command.

## Results

```text
CLI dispatch passed root+nested=yes default=8.5 symlink=canonical local-override=yes missing=actionable env=yes exit=37 composer=8.4,8.5 http-match=yes
```

- Linked project roots and nested directories selected PHP 8.4 or 8.5 as
  configured.
- A path containing spaces and non-ASCII characters worked normally.
- A symlinked working directory was canonicalized and matched its real linked
  project root.
- A nearer `.paddock.json` selected PHP 8.4 inside a PHP 8.5 linked project,
  proving nearest-project precedence.
- A directory outside every recognized project used default PHP 8.5.
- PHP 9.9 produced exit 78 with the selecting config path and
  `paddock php install 9.9` remediation.
- An environment sentinel reached PHP unchanged and an intentional PHP exit 37
  became the dispatcher exit status.
- Arguments following dispatcher `--` reached PHP without reinterpretation.
- Composer 2.8.12 reported PHP 8.4.23 in project A and PHP 8.5.8 in project B.
- Temporary HTTP fixtures reported the same PHP versions as CLI selection.
- No system `php` or `composer` command existed before or after the probe;
  `.bashrc` retained its prior timestamp and no shell startup file was edited.
- All temporary servers, projects, logs, and runtime downloads were removed.

## Decision

Adopt this selection and execution contract for the production CLI. The Python
file is a behavioral prototype, not a commitment to ship Python. Production
state must preserve the same canonical-path, nearest-candidate, explicit
version-only project config, `exec` passthrough, and actionable failure rules.
