# Experiment 0.6: Wildcard `.test` DNS

## Status

- State: passed
- Date started: 2026-08-16
- Existing resolver: `systemd-resolved`
- Local DNS responder: dnsmasq 2.93

## Proposed proof topology

Run a narrowly configured, root-managed dnsmasq process on loopback port 53.
Route only the `.test` DNS suffix to it through a dedicated NetworkManager
dummy link and systemd-resolved route-only domain:

```text
libc / browser / curl
        |
systemd-resolved
        |
route-only domain ~test on paddock-dns0
        |
127.0.0.1:53 (system dnsmasq)
        |
127.0.0.1 for *.test
```

dnsmasq must not read project or site configuration. Its only product-specific
rule is a constant wildcard mapping for the reserved `.test` suffix. Caddy owns
the mutable site map and returns its normal unknown-site response when a name is
not linked.

## Proposed address policy

- Every `.test` name, including nested names such as `api.project.test`, maps to
  `127.0.0.1`.
- IPv6 is initially explicit: do not publish `::1` until Caddy's listeners and
  test matrix cover IPv6 consistently.
- Names outside `.test` never reach the Paddock resolver.

## Safety boundaries

- The dnsmasq listener binds only loopback. Port 53 requires a small,
  root-managed system service in production.
- It provides no DHCP, upstream forwarding, cache, or network-facing service.
- A temporary NetworkManager dummy connection carries only `~test`; the probe
  deletes it during cleanup.
- Public-domain answers are recorded before, during, and after integration.
- Existing `.test` routing or listeners on ports 53/53535 are treated as a
  conflict, not overwritten.
- The proof will not edit `/etc/hosts`.

## Results

- Direct dnsmasq queries passed for wildcard, nested, and previously unseen
  `.test` names; non-`.test` queries were refused.
- NetworkManager successfully published a dedicated `paddock-dns0` dummy link
  to systemd-resolved with `DNS Domain: ~test` and `Default Route: no`.
- libc/NSS resolved `anything.test` and `api.project.test` to `127.0.0.1`.
- `curl` reached the PHP fixture through `anything.test`.
- Public DNS succeeded before, during, and after the route-only rule.
- Cleanup deleted the temporary connection and interface; `.test` stopped
  resolving afterward.
- The active Tailscale split-DNS profile and Wi-Fi default DNS profile were not
  modified.
- The Codex in-app browser rejected plain HTTP on the custom hostname with
  `ERR_BLOCKED_BY_CLIENT` before page load. No connected desktop Chrome browser
  was available. Browser validation remains open and should be repeated over
  trusted HTTPS during Experiment 0.7.

Probe result:

```text
resolved integration passed public_before=104.20.23.154 public_during=172.66.147.243 public_after=104.20.23.154
```

## Design findings

- NetworkManager ignores DNS attached to its loopback profile on this system.
- A dedicated dummy connection correctly creates the systemd-resolved routing
  scope without modifying Wi-Fi or VPN connections.
- NetworkManager did not preserve the experimental nonstandard DNS port when
  publishing the server to systemd-resolved. The responder must therefore use
  loopback port 53 and run behind a minimal privileged system unit.

## Production question

Production needs two durable, minimal privileged units: dnsmasq bound to
`127.0.0.1:53`, and a NetworkManager dummy connection publishing only `~test`
to systemd-resolved. Mutable project state remains in Caddy and outside this
privileged boundary.

## Package prerequisite

Install dnsmasq through Omarchy in an interactive terminal:

```bash
omarchy pkg add dnsmasq
```

The packaged global dnsmasq service remains disabled. The integration proof uses
a temporary systemd unit with `dnsmasq-system.conf`; the eventual installer will
install a dedicated Paddock unit rather than alter the distribution config.
