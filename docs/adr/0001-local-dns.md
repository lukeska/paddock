# ADR 0001: Local `.test` DNS

- Status: accepted for implementation
- Date: 2026-08-17
- Experiment: [Wildcard DNS](../../experiments/phase-0/dns/README.md)

## Decision

Run a narrowly configured system dnsmasq responder on `127.0.0.1:53`. It is
authoritative only for `.test`, maps every exact/nested name to `127.0.0.1`,
does not provide DHCP, and does not forward public queries.

Publish `~test` to systemd-resolved through a dedicated NetworkManager dummy
connection. Do not modify Wi-Fi, VPN, Tailscale, `/etc/hosts`, or the global
resolver. Caddy—not DNS—owns the mutable linked-site map.

Installation refuses an existing `.test` route, Paddock connection name, or
conflicting loopback port owner. Uninstall deletes only the named connection
and DNS unit/config, then verifies `.test` no longer resolves and public DNS is
unchanged.

## Rationale

NetworkManager ignored DNS attached directly to loopback on the tested system.
The dummy link correctly produced a route-only systemd-resolved scope with
`Default Route: no`. NetworkManager did not preserve a custom DNS port, so the
responder requires loopback port 53 behind a minimal system service.

## Evidence

Wildcard, nested, and previously unseen `.test` names resolved through direct
DNS and libc; curl reached the fixture by hostname. Public DNS succeeded before,
during, and after activation. Wi-Fi and Tailscale split DNS were not modified.
Cleanup removed the dummy link and `.test` resolution. Trusted HTTPS later
loaded successfully in an interactive browser through the same route.

