# ADR 0002: Local TLS Ownership and Trust

- Status: accepted for implementation
- Date: 2026-08-16
- Experiment: [Local TLS and browser trust](../../experiments/phase-0/tls/README.md)

## Context

Paddock must serve linked `.test` sites over HTTPS without warnings in CLI
tools and browsers. Initial setup may request privilege to install a public
root certificate, but linking sites, adding aliases, issuing leaf certificates,
and renewing them must remain unprivileged. Uninstall must remove only
Paddock-owned trust and must not destroy private keys implicitly.

The experiment compared mkcert-managed certificates with Caddy's internal CA.
Both can issue secure certificates without root. Caddy's internal issuer,
however, attempted trust-store installation as a side effect of starting the
server unless explicitly disabled. It also couples CA lifecycle to Caddy's data
directory and generic local-authority identity.

## Decision

Paddock owns a dedicated local CA and uses mkcert for CA creation, explicit
trust installation, and leaf issuance. Caddy serves supplied leaf certificate
and key files; it does not own or install Paddock's root.

The initial implementation stores CA material under:

```text
~/.local/share/paddock/pki/
```

The directory is mode `0700`; the CA private key must be owner-only. The public
root may be readable. CA material is never committed, transmitted, included in
logs, or copied into per-project directories.

Trust installation is a separate, explicit setup action. It installs only the
public root into Arch's system p11-kit anchors and supported user NSS stores.
Starting Caddy must never prompt for privilege or mutate trust.

## Certificate naming policy

- Issue one leaf certificate per linked site.
- Always include the exact canonical hostname, such as `project.test`.
- Include `*.project.test` only when the site enables subdomain aliases.
- Explicit non-subdomain aliases are individual SAN entries.
- Do not issue or serve a global `*.test` certificate.
- Remember that `*.project.test` covers one label only; it does not cover
  `a.b.project.test`.

This minimizes the effect of a leaked leaf key and makes alias coverage visible
in generated state.

## Issuance and renewal

Leaf issuance reads the user-owned CA key and therefore requires no root after
initial trust setup. Paddock regenerates a leaf when its hostname/SAN set
changes or before expiry, writes certificate and key atomically, validates SANs
and permissions, then performs a validated Caddy reload. Renewal does not
replace or reinstall the root CA.

Leaf private keys must be mode `0600` and stored in Paddock-owned state, not
inside the linked project. Old leaf material may be removed after a successful
reload and rollback window.

## Uninstall semantics

Normal uninstall performs two distinct actions:

1. Remove Paddock's public root from system and NSS trust stores by matching
   the owned certificate, then verify unrelated anchors remain.
2. Ask separately whether to delete the retained CA directory and private key.

Removing trust must not silently destroy the CA key. Deleting CA material is an
explicit destructive choice because it prevents future issuance from the same
authority. Project files and unrelated local CAs are never removed.

## Consequences

- Setup has one clear privileged trust operation.
- Normal linking, alias changes, issuance, renewal, and serving are
  unprivileged.
- Paddock must package or depend on a compatible mkcert implementation and
  test trust behavior across supported browsers.
- The installer and uninstaller must track the root fingerprint and verify
  every trust-store mutation.
- Caddy configurations must use supplied `tls <cert> <key>` paths and must not
  use automatic internal-CA trust installation.

## Evidence

- CA directory mode `0700`, CA key mode `0400`, leaf key mode `0600`.
- System p11-kit and NSS trust both accepted the root.
- Curl and an interactive browser loaded trusted `.test` HTTPS.
- Exact and deliberate wildcard SANs succeeded; an unrelated hostname failed.
- New leaves were issued without sudo before and after trust removal.
- Trust removal removed the Paddock root, left 170 unrelated system anchors,
  and caused curl verification to fail as expected.
- Reinstall restored the same SHA-256 fingerprint and trusted HTTPS:
  `5C:0D:C9:FD:D2:BA:0B:CB:DF:48:AF:41:FD:F8:C3:92:FA:9D:9F:37:B5:D9:5D:67:96:4C:1B:29:3B:56:C3:33`.

