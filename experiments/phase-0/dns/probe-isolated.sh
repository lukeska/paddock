#!/usr/bin/env bash

set -euo pipefail

script_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
php_binary=${PHP_BIN:-php}
port=${DNS_PORT:-53535}
temporary_dir=$(mktemp -d)
dnsmasq_pid=""

cleanup() {
  if [[ -n "$dnsmasq_pid" ]] && kill -0 "$dnsmasq_pid" 2>/dev/null; then
    kill "$dnsmasq_pid" 2>/dev/null || true
    wait "$dnsmasq_pid" 2>/dev/null || true
  fi
  rm -rf -- "$temporary_dir"
}
trap cleanup EXIT

sed \
  -e "s|@PORT@|$port|g" \
  -e "s|@LOG@|$temporary_dir/dnsmasq.log|g" \
  -e "s|@PID@|$temporary_dir/dnsmasq.pid|g" \
  "$script_root/dnsmasq.conf.in" >"$temporary_dir/dnsmasq.conf"

dnsmasq --test --conf-file="$temporary_dir/dnsmasq.conf"
dnsmasq --conf-file="$temporary_dir/dnsmasq.conf" \
  >"$temporary_dir/stdout.log" 2>&1 &
dnsmasq_pid=$!

for _ in {1..40}; do
  if "$php_binary" "$script_root/query.php" 127.0.0.1 "$port" anything.test 127.0.0.1 \
    >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$dnsmasq_pid" 2>/dev/null; then
    printf 'dnsmasq exited before becoming ready.\n' >&2
    sed -n '1,100p' "$temporary_dir/stdout.log" >&2
    exit 1
  fi
  sleep 0.05
done

"$php_binary" "$script_root/query.php" 127.0.0.1 "$port" anything.test 127.0.0.1
"$php_binary" "$script_root/query.php" 127.0.0.1 "$port" api.project.test 127.0.0.1
"$php_binary" "$script_root/query.php" 127.0.0.1 "$port" previously-unseen.test 127.0.0.1
"$php_binary" "$script_root/query.php" 127.0.0.1 "$port" example.com REFUSED

if ! rg --quiet 'query\[A\].*api\.project\.test' "$temporary_dir/dnsmasq.log"; then
  printf 'dnsmasq query log did not record the nested wildcard query.\n' >&2
  exit 1
fi

printf 'isolated DNS passed port=%s pid=%s\n' "$port" "$dnsmasq_pid"

