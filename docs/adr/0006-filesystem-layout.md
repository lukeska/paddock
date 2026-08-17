# ADR 0006: Filesystem Layout and Ownership

- Status: accepted for implementation
- Date: 2026-08-16
- Experiment: [XDG layout](../../experiments/phase-0/layout/README.md)

## Decision

Use XDG user directories with conventional fallbacks:

- config: `${XDG_CONFIG_HOME:-~/.config}/paddock`
- durable data/runtimes/PKI: `${XDG_DATA_HOME:-~/.local/share}/paddock`
- operational state/logs: `${XDG_STATE_HOME:-~/.local/state}/paddock`
- sockets/PIDs: `$XDG_RUNTIME_DIR/paddock`
- rebuildable downloads: `${XDG_CACHE_HOME:-~/.cache}/paddock`

Paddock roots are user-owned mode `0700`. Generated config and private state
default to `0600`; CA and leaf-key requirements may be stricter. Runtime sockets
are `0600` beneath the `0700` runtime root. Installed runtime executables may be
`0755`, but their parent tree remains Paddock-owned and atomically activated.

`XDG_RUNTIME_DIR` has no persistent fallback. Service startup fails directly if
it is unavailable, because placing sockets in durable or shared `/tmp` state
would weaken ownership and cleanup guarantees.

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
