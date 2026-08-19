# ADR 0006: Filesystem Layout and Ownership

- Status: accepted for implementation
- Date: 2026-08-16
- Amended: 2026-08-19 (sockets/PIDs moved out of `$XDG_RUNTIME_DIR`)
- Experiment: [XDG layout](../../experiments/phase-0/layout/README.md)

## Decision

Use XDG user directories with conventional fallbacks:

- config: `${XDG_CONFIG_HOME:-~/.config}/paddock`
- durable data/runtimes/PKI: `${XDG_DATA_HOME:-~/.local/share}/paddock`
- operational state/logs: `${XDG_STATE_HOME:-~/.local/state}/paddock`
- sockets/PIDs: `/run/paddock`, owned by systemd (see amendment below)
- rebuildable downloads: `${XDG_CACHE_HOME:-~/.cache}/paddock`

Paddock roots are user-owned mode `0700`. Generated config and private state
default to `0600`; CA and leaf-key requirements may be stricter. Runtime sockets
are `0600` beneath the `0700` runtime root. Installed runtime executables may be
`0755`, but their parent tree remains Paddock-owned and atomically activated.

Runtime scope has no persistent fallback. Placing sockets in durable or shared
`/tmp` state would weaken ownership and cleanup guarantees.

## Amendment: runtime scope is systemd-owned, not session-owned

Sockets and PIDs were originally specified as `$XDG_RUNTIME_DIR/paddock`. That
is wrong for the units ADR 0005 defines. PHP-FPM runs as a *system* unit, so:

- At boot the generated unit named `/run/user/<uid>/paddock` in
  `ReadWritePaths=`. `user-runtime-dir@<uid>.service` creates
  `/run/user/<uid>` but never the Paddock child, and systemd builds the mount
  namespace before `ExecStart`, so every PHP unit failed with
  `226/NAMESPACE`. The directory only ever existed because an earlier
  interactive run had created it.
- `/run/user/<uid>` belongs to the login session. Depending on it contradicts
  ADR 0005, which requires the stack to start before any login and to keep
  serving after logout with linger disabled.

Sockets and PIDs therefore live at `/run/paddock/php/<minor>`, created and
removed by `RuntimeDirectory=paddock/php/%i` with
`RuntimeDirectoryMode=0700` in `paddock-php@.service`. systemd creates the
directory with the unit's own user before the namespace is set up, makes it
writable without a `ReadWritePaths=` entry under `ProtectSystem=strict`, and
removes only that instance's directory on stop, leaving a sibling version's
socket untouched. Paddock never creates the directory itself: the CLI is
unprivileged and a fresh boot must not depend on it already existing.

The ownership guarantees are strengthened, not weakened. The socket stays
`0600` inside a `0700` per-version directory owned by the desktop user, while
the two generated parents (`/run/paddock` and `/run/paddock/php`) are
`root:root` `0755` and hold no sensitive entries: an unprivileged process can
no longer create or replace anything above the per-version directory.

The value in `paddock.paths.SYSTEM_RUNTIME_ROOT` and the unit's
`RuntimeDirectory=` must stay equal; a test pins that agreement.

System units and helpers use separately named, root-owned package paths. A
privileged unit never executes a user-writable command; user-owned config is
validated and interpreted only by processes already running as the desktop
user.

## Update and recovery

Durable generated files are written to same-directory mode-`0600` temporary
files, flushed, atomically renamed, and followed by directory `fsync`. Failure
before rename leaves the prior generation untouched and removes the candidate.
Runtime activation uses versioned directories plus atomic references; caches
are never the only copy of durable metadata.

Generated Caddy configuration, service projections, and runtime socket state
must be reproducible from durable site/runtime records. Projects themselves are
never copied, moved, or deleted by uninstall.

## Lifecycle

- Config and CA material require explicit retention/removal choices.
- Runtimes are durable but independently removable and reinstallable.
- State/logs may be retained for diagnosis or explicitly purged.
- Runtime sockets/PIDs are always ephemeral and regenerated.
- Cache is always safe to delete.
- System integration is removed from a package manifest, never by broad path
  deletion.

## Evidence

Disposable fake homes with spaces and non-ASCII characters passed. Explicit
XDG roots and all documented fallbacks resolved correctly. Root/file modes were
`0700`/`0600`. Missing runtime scope failed directly. Injected ENOSPC preserved
the previous bytes and left no temporary file; the next atomic update passed.
