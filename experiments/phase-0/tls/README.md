# Experiment 0.7: Local TLS and Browser Trust

## Status

- State: passed
- Date started: 2026-08-16
- Caddy: 2.11.4
- mkcert: 1.4.4
- System trust tooling: p11-kit `trust`
- NSS tooling: `certutil`

## Clean baseline

- mkcert's default CA directory does not exist.
- No mkcert, Caddy, or Paddock CA was found in the system anchors.
- No matching CA was found in the user's Chromium NSS database.
- Caddy's user data directory contains no internal PKI material.

## Candidate model

Keep CA ownership and certificate issuance in the unprivileged user account.
Installation performs one explicit privileged operation to add only the public
root certificate to system trust. Normal site linking and certificate renewal
must never require root.

Current recommendation: use a Paddock-owned CAROOT with mkcert for explicit
CA creation, trust installation, and leaf issuance. Give Caddy leaf certificate
and key paths; do not let `tls internal` own trust lifecycle implicitly.

The first probe is deliberately isolated: both candidate CAs and all leaf keys
live in a mode-0700 temporary directory, no trust store is changed, and cleanup
removes the directory.

## Naming policy under test

- Issue one leaf certificate per linked site.
- Include the exact site name and `*.site.test` only when alias support needs it.
- Do not use a global `*.test` certificate.
- Keep the CA private key separate from Caddy's serving state.

## Isolated probe results

```text
isolated TLS passed mkcert_root_mode=400 mkcert_leaf_mode=600 caddy_root_mode=600
```

- mkcert created the CA and a leaf containing `app-a.test`,
  `*.app-a.test`, and `app-b.test` without privilege elevation.
- mkcert used mode `0400` for the CA key and `0600` for the leaf key.
- Caddy's internal issuer created an owner-only mode-`0600` CA key and served
  HTTPS successfully when curl received its isolated root explicitly.
- Caddy attempted to add its root to the user's NSS database unless
  `skip_install_trust` was set. The temporary entry was removed and the probe
  now prevents automatic trust installation.
- Caddy also attempted an automatic port-80 redirect unless
  `auto_https disable_redirects` was set.
- No CA, leaf certificate, or key survived isolated-probe cleanup.

## Why mkcert currently leads

- Trust installation is an explicit command rather than a side effect of
  starting the web server.
- The CA can live in a clearly owned Paddock directory, independent of
  Caddy's general data directory.
- Site certificates and SANs are issued deliberately and can be regenerated
  without root after the one-time trust operation.
- Uninstall can target one named Paddock root rather than Caddy's generic
  local authority.

## Installed trust results

- CAROOT: `~/.local/share/paddock/pki`
- Directory mode: `0700`
- CA private-key mode: `0400`
- Public-root mode: `0644`
- System p11-kit trust: anchor present
- User NSS trust: TLS CA present
- SHA-256 fingerprint:
  `5C:0D:C9:FD:D2:BA:0B:CB:DF:48:AF:41:FD:F8:C3:92:FA:9D:9F:37:B5:D9:5D:67:96:4C:1B:29:3B:56:C3:33`

Post-install issuance required no sudo and produced a mode-`0600` leaf key.
Default curl trust verified the intended exact and wildcard names, while a
hostname outside the certificate SANs failed verification:

```text
trusted TLS passed exact=app-a.test wildcard=api.app-a.test unrelated=app-b.test-rejected port=18443
```

## Browser result

- `https://browser-check.test:18443/` loaded successfully in an interactive
  browser session through wildcard `.test` DNS.
- The browser displayed `paddock trusted tls ok` without a certificate
  warning.
- Automated navigation from the Codex browser controller was blocked by its
  client policy before networking, but manual navigation in the same in-app
  browser succeeded. This is a controller limitation, not a TLS or DNS failure.

## Trust-removal rehearsal

`CAROOT=~/.local/share/paddock/pki mkcert -uninstall` removed the public root
from both system p11-kit and the user's NSS database.

- The mode-`0700` CA directory, mode-`0400` private key, and public root were
  retained, so uninstall did not destroy user data implicitly.
- 170 unrelated system anchors remained after removal.
- The retained CA could still issue a new leaf certificate without sudo.
- Default curl rejected that leaf with certificate verification error 60,
  proving removal affected active trust.
- The temporary leaf and HTTPS server were cleaned up.

## Reinstall result

- Reinstall restored the root in both system p11-kit and user NSS trust.
- The SHA-256 fingerprint was unchanged.
- Directory and key permissions were unchanged.
- The trusted HTTPS probe passed again with a newly issued temporary leaf.

Decision: [ADR 0002](../../../docs/adr/0002-local-tls.md).
