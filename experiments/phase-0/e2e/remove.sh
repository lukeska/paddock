#!/usr/bin/env bash

set -euo pipefail

if (( EUID != 0 )); then
  printf 'Run with sudo and PADDOCK_USER set.\n' >&2
  exit 2
fi
desktop_user=${PADDOCK_USER:-${SUDO_USER:-}}
[[ -n "$desktop_user" && "$desktop_user" != root ]] || { printf 'PADDOCK_USER is required.\n' >&2; exit 2; }
desktop_home=$(getent passwd "$desktop_user" | cut -d: -f6)
state_dir="$desktop_home/.local/share/paddock/e2e-probe"
unit_dir=/etc/systemd/system
units=(paddock-e2e.target paddock-e2e-dns.service paddock-e2e-dns-route.service paddock-e2e-php84.service paddock-e2e-php85.service paddock-e2e-caddy.service)

systemctl disable --now paddock-e2e.target >/dev/null 2>&1 || true
nmcli connection delete paddock-e2e-dns >/dev/null 2>&1 || true
for unit in "${units[@]}"; do rm -f -- "$unit_dir/$unit"; done
rm -f -- /etc/paddock-e2e-dnsmasq.conf /usr/local/lib/paddock-e2e-wait-socket /usr/local/lib/paddock-e2e-check-ports
rm -rf -- "$state_dir"
systemctl daemon-reload
systemctl reset-failed >/dev/null 2>&1 || true
printf 'E2E setup removed; CA trust was intentionally retained for separate final restoration.\n'
