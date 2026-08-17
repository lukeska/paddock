# Experiment 0.11: Filesystem Ownership and XDG Layout

## Status

- State: passed
- Date started: 2026-08-16
- Real user directories modified: none

## Artifact matrix

| Artifact | Path | Owner/mode | Lifecycle | Backup/uninstall |
| --- | --- | --- | --- | --- |
| User/generated config | `$XDG_CONFIG_HOME/paddock` | user, `0700`; files `0600` | durable | back up; remove on explicit config purge |
| Runtimes and CA | `$XDG_DATA_HOME/paddock` | user, `0700`; runtime files executable as needed; private keys owner-only | durable | runtimes removable; CA key separate explicit choice |
| Logs/operational state | `$XDG_STATE_HOME/paddock` | user, `0700` | durable but expendable | optional retention/removal |
| Sockets/PIDs | `$XDG_RUNTIME_DIR/paddock` | user, `0700`; sockets `0600` | boot/session ephemeral | never backed up; always regenerated |
| Downloads/build cache | `$XDG_CACHE_HOME/paddock` | user, `0700` | rebuildable | always safe to remove |
| System units/helpers | package-owned `/etc`/`/usr` paths | root-owned, not user-writable | installed | remove by manifest only |

Fallbacks follow the XDG Base Directory convention: config `~/.config`, data
`~/.local/share`, state `~/.local/state`, and cache `~/.cache`. A missing
`XDG_RUNTIME_DIR` is an error for service startup; Paddock does not invent a
persistent socket directory.

## Update contract

Generated durable files use same-directory temporary files, `fsync`, atomic
rename, and directory `fsync`. An injected write/space failure must leave the
old generation byte-for-byte intact and remove the temporary candidate.

## Result

```text
XDG layout passed unicode+spaces=yes modes=0700 files=0600 fallbacks=yes runtime-required=yes atomic=yes enospc=rollback
```

All paths were created beneath disposable fake homes. The injected ENOSPC left
generation 1 intact, removed its temporary candidate, and a subsequent update
atomically installed generation 2. No real Paddock directory was created.

Decision: [ADR 0006](../../../docs/adr/0006-filesystem-layout.md).

