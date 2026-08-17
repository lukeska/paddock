# Experiment 0.1: System Baseline and Conflict Inventory

## Result

- Status: complete for the current representative machine
- Date: 2026-08-15
- Change policy: read-only inspection
- System changes made: none
- Cleanup required: none

This inventory describes the machine at the start of Phase 0. It is evidence for
architecture decisions, not a claim that every Omarchy installation has the same
state. Installation logic must detect these conditions rather than assume them.

## Platform baseline

| Item | Observed value |
| --- | --- |
| Distribution | Omarchy 4.0.0 |
| Omarchy package | 4.0.0-1 |
| Omarchy channel | stable |
| Distribution lineage | Arch Linux |
| Kernel | Linux 7.1.8-arch1-3 |
| Architecture | x86_64 |
| Init/service manager | systemd 261.2-1 |
| Network manager | NetworkManager 1.58.0-1 |
| XDG runtime directory | available for the desktop user |

The inventory intentionally omits the local username, hostname, DNS search
domain, and unrelated diagnostic output.

## Relevant installed software

| Component | State | Version or detail |
| --- | --- | --- |
| Caddy | not installed | No binary or unit found |
| nginx | not installed | No binary or unit found |
| Apache/httpd | not installed | No binary or unit found |
| System PHP | not installed | No `php` or `php-fpm` binary found on `PATH` |
| dnsmasq | not installed | No binary or unit found |
| systemd-resolved | installed and active | Enabled system service |
| `mkcert` | not installed | No binary found |
| NSS tools | installed | `nss` 3.126-1 and `certutil` available |
| Chromium | installed | `/usr/bin/chromium` |
| Firefox | installed | `/usr/bin/firefox` |
| Docker | installed but inactive | Docker 29.7.2; Compose 5.4.0 |
| Podman | not installed | No binary or unit found |
| UFW | installed | 0.36.2-7 |
| nftables | installed | 1.1.6-3 |
| Go | not installed | No binary found on `PATH` |
| Composer | not installed globally | A bundled copy exists under Herd Lite |

No active Caddy, nginx, Apache, PHP-FPM, dnsmasq, Docker, or Podman process was
observed.

## Resolver baseline

`/etc/resolv.conf` is a symbolic link to systemd-resolved's stub configuration:

```text
/run/systemd/resolve/stub-resolv.conf
```

The configured local nameserver is `127.0.0.53`, with EDNS and DNSSEC trust-ad
options. NSS host lookup order is:

```text
mymachines mdns_minimal [NOTFOUND=return] resolve files myhostname dns
```

Relevant resolved drop-ins already exist:

- `10-disable-multicast.conf` disables LLMNR and multicast DNS.
- `20-docker-dns.conf` adds a DNS stub listener on `172.17.0.1`.

The current listeners are:

| Address | Port | Purpose inferred from configuration |
| --- | ---: | --- |
| `127.0.0.53` | 53 TCP/UDP | systemd-resolved local stub |
| `127.0.0.54` | 53 TCP/UDP | systemd-resolved proxy stub |
| `172.17.0.1` | 53 TCP/UDP | Docker bridge resolved listener |

An arbitrary test name under `.test` did not resolve. This means the suffix is
currently available from the resolver's perspective, but port 53 is not globally
unused. Paddock must integrate with the active resolved setup instead of
attempting to replace or bind over its existing listeners.

## Web-port and socket baseline

- No listener was found on TCP port 80.
- No listener was found on TCP port 443.
- No PHP-FPM Unix socket was found.
- No existing candidate Caddy, nginx, or PHP-FPM binary has a low-port
  capability, because those binaries are not installed.

Ports 80 and 443 are therefore available at this point in time. Installation
must still perform a fresh conflict check because port ownership is mutable.

## Certificate trust baseline

- Chromium and Firefox are both present and must be included in the TLS test.
- The user has an NSS database for Chromium-family applications.
- The user has a Firefox profile certificate database.
- No system trust anchor with a name suggesting Herd, Valet, Caddy, `mkcert`, or
  Paddock was found in the standard inspected anchor directories.

Absence by filename is not cryptographic proof that no local development CA is
trusted. Experiment 0.7 must inspect trust-store contents by certificate identity
before installing a CA.

## Existing local-development tools

### Herd Lite

An existing user-owned directory was found at `~/.config/herd-lite`. It contains:

- A PHP executable.
- A dedicated `php.ini`.
- Composer and Laravel executables.
- A CA certificate bundle.
- An uninstall script.

The bundled runtime reports PHP 8.5.0, NTS, x86_64 Linux, built against musl by
Beyond Code's `php.new` build system. It loads its explicit user-owned `php.ini`
but reports `/usr/local/etc/php` as its compiled configuration prefix.

No `herd` or `herd-lite` command is currently on `PATH`, and no associated
process or standard-port listener was observed. Nothing in this directory was
changed.

This is not currently a runtime collision, but it is highly relevant evidence
for Experiment 0.3: relocatable PHP builds already work on this machine and the
build provenance may be a candidate worth evaluating. Paddock must not adopt,
update, or delete this existing runtime as if it owns it.

### Herdr

The installed `herdr` package and `~/.config/herdr` directory initially matched
the name-based search. Inspection showed that Herdr is Omarchy's terminal
workspace manager for coding agents, not a PHP development environment.

- Package: `herdr` 0.8.0.r13-1
- Command: `/usr/bin/herdr`
- Functional overlap with Paddock: none identified
- Name/path collision with Paddock: none identified

Herdr is therefore a false positive and requires no integration work.

### Other tools

No Valet, DDEV, or Lando command, package, or matching user configuration
directory was found in the inspected locations.

## Proposed-path collision check

All proposed Paddock paths were clear at inventory time:

```text
~/.config/paddock
~/.local/share/paddock
~/.local/state/paddock
~/.cache/paddock
${XDG_RUNTIME_DIR}/paddock
~/.config/omarchy/plugins/paddock
```

No `paddock` or `omarchy-paddock` command was found on `PATH`.

## Omarchy integration observations

- Omarchy's supported package-facing commands are `omarchy pkg add` and
  `omarchy pkg aur add`.
- Omarchy's `plugin` command manages Quickshell plugins, not system development
  environments.
- Omarchy-owned files under `/usr/share/omarchy` remain out of scope for writes.
- No current Paddock command route exists.

The primary command should remain `paddock` unless a later experiment proves a
stable, public discovery mechanism for `omarchy paddock`.

## Conflict matrix

| Area | Current owner/state | Risk | Required handling |
| --- | --- | --- | --- |
| `.test` suffix | No observed resolver rule | Low now, mutable | Probe before install and report competing wildcard rules |
| Port 53 loopback | systemd-resolved | High | Integrate with resolved; do not replace or bind over its stubs |
| Docker bridge DNS | resolved at `172.17.0.1:53` | Medium | Preserve the existing Docker drop-in and listener |
| Port 80 | Free at inventory time | Medium | Recheck immediately before binding |
| Port 443 | Free at inventory time | Medium | Recheck immediately before binding |
| PHP-FPM sockets | None observed | Low | Use a namespaced runtime directory |
| System PHP | Not installed | Low now | Never depend on or overwrite system PHP |
| Herd Lite runtime | User-owned PHP 8.5.0 | High ownership risk | Treat as foreign, read-only state; use only as comparative evidence |
| Browser trust | Chromium NSS and Firefox DBs exist | Medium | Test trust in both; identify certs, not filenames alone |
| Docker | Installed, service inactive | Medium | Do not assume daemon availability for core features |
| Firewall | UFW and nftables installed | Medium | Inspect active rules before standard-port testing |
| Omarchy package files | Owned by Omarchy | Critical | Never modify `/usr/share/omarchy` |
| Omarchy plugins | Quickshell-only boundary | High design risk | Keep core runtime independent; optional shell companion later |
| Paddock XDG paths | Clear | Low | Still refuse unsafe adoption of unexpected existing content |

## Architecture implications

1. systemd-resolved integration should be evaluated before dnsmasq replacement.
   The machine already uses resolved for libc lookups and Docker bridge DNS.
2. Port 53 cannot be treated as simply free. A separate local authoritative
   resolver must use a non-conflicting address/port and be routed through
   resolved, or resolved must implement the required route directly.
3. Caddy, PHP, and `mkcert` need to be supplied by installation or packaging;
   the baseline has none of them.
4. Docker cannot be a core-runtime dependency because its service is inactive,
   though it remains a viable optional service backend.
5. TLS validation must cover both Chromium and Firefox trust models.
6. Herd Lite's relocatable PHP is a concrete input to the PHP distribution
   comparison, not a runtime Paddock may take over.
7. Standard HTTP ports currently have no collision, making the capability and
   service-ownership experiment viable without first removing another server.

## Commands used

The inventory used read-only forms of:

- `uname`, `/etc/os-release`, `omarchy version`, and `omarchy channel current`.
- `pacman -Q` and `pacman -Qi`.
- `command -v` and tool `--version`/`--help` output.
- `systemctl is-enabled`, `systemctl is-active`, and service listings.
- `ss` and kernel socket tables under `/proc/net`.
- `readlink`, `stat`, `find`, and filtered reads of resolver configuration.
- `getent ahosts` for a deliberately nonexistent `.test` name.
- Process listings filtered to relevant component names.

Raw output was not committed because it contains machine-specific identifiers
and rapidly stale operational detail. This document records the relevant,
sanitized observations.

## Limitations and revalidation requirements

- This is one x86_64 Omarchy 4.0 machine, not a clean virtual machine image.
- The system has user-installed software, notably Herd Lite and Docker.
- Service and port state can change after this inventory.
- Firewall package presence was confirmed, but active firewall policy was not
  inspected in this read-only baseline.
- VPN behavior was not exercised.
- ARM64 availability was not assessed.
- Browser trust entries were not enumerated by certificate identity.
- No package repository or network availability was tested.
- No reboot, logout, or service restart occurred.

Every destructive or privileged Phase 0 experiment must take a fresh targeted
snapshot of the state it is about to change.

## Decision

Experiment 0.1 passes for this representative machine. There is no existing web
server or PHP-FPM runtime blocking the next experiment. DNS must be designed
around active systemd-resolved listeners, and the existing Herd Lite runtime
must be preserved as foreign user data.

The next task is Experiment 0.2: create reproducible minimal PHP fixtures before
installing or selecting a PHP distribution strategy.
