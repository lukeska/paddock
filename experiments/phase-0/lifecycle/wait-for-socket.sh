#!/usr/bin/env bash

set -euo pipefail

socket=${1:-}
if [[ -z "$socket" ]]; then
  printf 'Usage: %s <socket>\n' "$0" >&2
  exit 2
fi

for _ in {1..100}; do
  [[ -S "$socket" ]] && exit 0
  sleep 0.05
done

printf 'Timed out waiting for socket: %s\n' "$socket" >&2
exit 1

