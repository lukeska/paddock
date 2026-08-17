#!/usr/bin/env bash

set -euo pipefail

script_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
caroot=${CAROOT:-"$HOME/.local/share/paddock/pki"}
https_port=${HTTPS_PORT:-18443}
temporary_dir=$(mktemp -d)
caddy_pid=""

cleanup() {
  if [[ -n "$caddy_pid" ]] && kill -0 "$caddy_pid" 2>/dev/null; then
    kill "$caddy_pid" 2>/dev/null || true
    wait "$caddy_pid" 2>/dev/null || true
  fi
  rm -rf -- "$temporary_dir"
}
trap cleanup EXIT

chmod 700 "$temporary_dir"
certificate="$temporary_dir/app-a.pem"
private_key="$temporary_dir/app-a-key.pem"

CAROOT="$caroot" mkcert \
  -cert-file "$certificate" \
  -key-file "$private_key" \
  app-a.test '*.app-a.test' >/dev/null

if [[ $(stat -c '%a' "$private_key") != 600 ]]; then
  printf 'Leaf private key is not mode 0600.\n' >&2
  exit 1
fi

sed \
  -e "s|@CERTIFICATE@|$certificate|g" \
  -e "s|@PRIVATE_KEY@|$private_key|g" \
  -e "s|:18443|:$https_port|g" \
  "$script_root/Caddyfile.trusted.in" >"$temporary_dir/Caddyfile"

caddy run --config "$temporary_dir/Caddyfile" --adapter caddyfile \
  >"$temporary_dir/caddy.log" 2>&1 &
caddy_pid=$!

for _ in {1..80}; do
  if curl --fail --silent --noproxy '*' \
      --resolve "app-a.test:$https_port:127.0.0.1" \
      "https://app-a.test:$https_port/" >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done

for hostname in app-a.test api.app-a.test; do
  body=$(curl --fail --silent --noproxy '*' \
    --resolve "$hostname:$https_port:127.0.0.1" \
    "https://$hostname:$https_port/")
  if [[ "$body" != 'paddock trusted tls ok' ]]; then
    printf 'Unexpected HTTPS body for %s: %s\n' "$hostname" "$body" >&2
    exit 1
  fi
done

if curl --fail --silent --noproxy '*' \
    --resolve "app-b.test:$https_port:127.0.0.1" \
    "https://app-b.test:$https_port/" >/dev/null 2>&1; then
  printf 'Certificate unexpectedly covered app-b.test.\n' >&2
  exit 1
fi

printf 'trusted TLS passed exact=app-a.test wildcard=api.app-a.test unrelated=app-b.test-rejected port=%s\n' \
  "$https_port"
