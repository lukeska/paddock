#!/usr/bin/env bash

set -euo pipefail

listeners=$(ss -H -ltnup | awk '
  ($1 == "tcp" && $5 ~ /:(80|443)$/) ||
  ($1 == "udp" && $5 ~ /:443$/)
')

if [[ -n "$listeners" ]]; then
  printf 'Paddock cannot start: required loopback web ports are already in use.\n' >&2
  printf '%s\n' "$listeners" >&2
  exit 1
fi

printf 'ports 80/tcp, 443/tcp, and 443/udp are available\n'
