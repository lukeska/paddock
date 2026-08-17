#!/usr/bin/env bash

set -euo pipefail

if (( EUID != 0 )); then
  printf 'Run with sudo and PADDOCK_USER set.\n' >&2
  exit 2
fi
desktop_user=${PADDOCK_USER:-${SUDO_USER:-}}
[[ -n "$desktop_user" && "$desktop_user" != root ]] || { printf 'PADDOCK_USER is required.\n' >&2; exit 2; }
desktop_home=$(getent passwd "$desktop_user" | cut -d: -f6)
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

systemctl restart paddock-e2e-php84.service
systemctl restart paddock-e2e-caddy.service
runuser --user "$desktop_user" -- env HOME="$desktop_home" "$script_dir/smoke.sh"
printf 'targeted restart E2E passed\n'
