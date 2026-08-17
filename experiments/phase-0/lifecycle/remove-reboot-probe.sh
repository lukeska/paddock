#!/usr/bin/env bash

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  printf 'Run this cleanup with sudo.\n' >&2
  exit 2
fi

desktop_user=${PADDOCK_USER:-${SUDO_USER:-}}
if [[ -z "$desktop_user" ]]; then
  printf 'PADDOCK_USER or SUDO_USER is required.\n' >&2
  exit 2
fi
desktop_home=$(getent passwd "$desktop_user" | cut -d: -f6)
state_dir="$desktop_home/.local/share/paddock/reboot-probe"
unit_root=/etc/systemd/system

systemctl disable --now paddock-reboot.target >/dev/null 2>&1 || true
rm -f -- \
  "$unit_root/paddock-reboot.target" \
  "$unit_root/paddock-reboot-php84.service" \
  "$unit_root/paddock-reboot-php85.service" \
  "$unit_root/paddock-reboot-caddy.service" \
  /usr/local/lib/paddock-reboot-wait-for-socket
systemctl daemon-reload
systemctl reset-failed >/dev/null 2>&1 || true
rm -rf -- "$state_dir"

printf 'reboot probe removed\n'
