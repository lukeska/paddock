#!/usr/bin/env bash

set -euo pipefail

script_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
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
mkdir -p "$temporary_dir/mkcert" "$temporary_dir/caddy-data" "$temporary_dir/caddy-config"
chmod 700 "$temporary_dir/mkcert" "$temporary_dir/caddy-data" "$temporary_dir/caddy-config"

CAROOT="$temporary_dir/mkcert" mkcert \
  -cert-file "$temporary_dir/mkcert/app-a.pem" \
  -key-file "$temporary_dir/mkcert/app-a-key.pem" \
  app-a.test '*.app-a.test' app-b.test >/dev/null

mkcert_root_mode=$(stat -c '%a' "$temporary_dir/mkcert/rootCA-key.pem")
mkcert_leaf_mode=$(stat -c '%a' "$temporary_dir/mkcert/app-a-key.pem")
if [[ ${mkcert_root_mode: -2} != 00 || ${mkcert_leaf_mode: -2} != 00 ]]; then
  printf 'mkcert key permissions are too broad: root=%s leaf=%s\n' \
    "$mkcert_root_mode" "$mkcert_leaf_mode" >&2
  exit 1
fi

mkcert_sans=$(openssl x509 -in "$temporary_dir/mkcert/app-a.pem" -noout -ext subjectAltName)
for expected_name in 'DNS:app-a.test' 'DNS:*.app-a.test' 'DNS:app-b.test'; do
  if [[ "$mkcert_sans" != *"$expected_name"* ]]; then
    printf 'mkcert certificate is missing %s.\n' "$expected_name" >&2
    exit 1
  fi
done

XDG_DATA_HOME="$temporary_dir/caddy-data" \
XDG_CONFIG_HOME="$temporary_dir/caddy-config" \
  caddy run --config "$script_root/Caddyfile.isolated" --adapter caddyfile \
  >"$temporary_dir/caddy.log" 2>&1 &
caddy_pid=$!

caddy_root="$temporary_dir/caddy-data/caddy/pki/authorities/local/root.crt"
for _ in {1..80}; do
  if [[ -f "$caddy_root" ]] && \
     curl --fail --silent \
       --noproxy '*' \
       --resolve app-a.test:18443:127.0.0.1 \
       --cacert "$caddy_root" \
       https://app-a.test:18443/ >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done

if [[ ! -f "$caddy_root" ]] || ! kill -0 "$caddy_pid" 2>/dev/null; then
  printf 'Caddy did not initialize its isolated CA. Log follows:\n' >&2
  sed -n '1,160p' "$temporary_dir/caddy.log" >&2
  exit 1
fi

if ! body=$(curl --fail --silent \
  --noproxy '*' \
  --resolve app-a.test:18443:127.0.0.1 \
  --cacert "$caddy_root" \
  https://app-a.test:18443/); then
  printf 'Caddy HTTPS request failed. Log follows:\n' >&2
  sed -n '1,160p' "$temporary_dir/caddy.log" >&2
  exit 1
fi
if [[ "$body" != 'caddy internal tls ok' ]]; then
  printf 'Caddy internal TLS returned unexpected body: %s\n' "$body" >&2
  exit 1
fi

caddy_root_key="$temporary_dir/caddy-data/caddy/pki/authorities/local/root.key"
caddy_root_mode=$(stat -c '%a' "$caddy_root_key")
if [[ ${caddy_root_mode: -2} != 00 ]]; then
  printf 'Caddy root key permissions are too broad: %s\n' "$caddy_root_mode" >&2
  exit 1
fi

printf 'isolated TLS passed mkcert_root_mode=%s mkcert_leaf_mode=%s caddy_root_mode=%s\n' \
  "$mkcert_root_mode" "$mkcert_leaf_mode" "$caddy_root_mode"
