#!/usr/bin/env bash

set -euo pipefail

script_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd "$script_root/../../.." && pwd)
fixture_root="$repository_root/experiments/phase-0/fixtures"
php_binary=${PHP_BIN:-php}
dns_port=${DNS_PORT:-53535}
http_port=${HTTP_PORT:-18081}
manage_dnsmasq=${MANAGE_DNSMASQ:-true}
temporary_dir=$(mktemp -d)
dnsmasq_pid=""
http_pid=""
route_active=false
resolver_connection=paddock-dns-probe
resolver_interface=paddock-dns0

stop_process() {
  local pid=$1
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  fi
}

cleanup() {
  if [[ "$route_active" == true ]]; then
    nmcli connection delete "$resolver_connection" >/dev/null 2>&1 || true
  fi
  stop_process "$http_pid"
  stop_process "$dnsmasq_pid"
  rm -rf -- "$temporary_dir"
}
trap cleanup EXIT

if getent ahostsv4 paddock-conflict-check.test >/dev/null 2>&1; then
  printf 'A .test resolver is already active; refusing to overwrite it.\n' >&2
  exit 1
fi

if nmcli -g NAME connection show | grep -Fxq "$resolver_connection" ||
   nmcli -g DEVICE device status | grep -Fxq "$resolver_interface"; then
  printf 'The probe resolver connection or interface already exists; refusing to overwrite it.\n' >&2
  exit 1
fi

public_before=$(getent ahostsv4 example.com | awk 'NR == 1 { print $1 }')
if [[ -z "$public_before" ]]; then
  printf 'Public DNS did not work before the experiment.\n' >&2
  exit 1
fi

if [[ "$manage_dnsmasq" == true ]]; then
  sed \
    -e "s|@PORT@|$dns_port|g" \
    -e "s|@LOG@|$temporary_dir/dnsmasq.log|g" \
    -e "s|@PID@|$temporary_dir/dnsmasq.pid|g" \
    "$script_root/dnsmasq.conf.in" >"$temporary_dir/dnsmasq.conf"

  dnsmasq --conf-file="$temporary_dir/dnsmasq.conf" \
    >"$temporary_dir/dnsmasq.stdout.log" 2>&1 &
  dnsmasq_pid=$!
fi

"$php_binary" -S "127.0.0.1:$http_port" \
  -t "$fixture_root/app-php-a/public" \
  "$fixture_root/shared/router.php" \
  >"$temporary_dir/http.log" 2>&1 &
http_pid=$!

for _ in {1..40}; do
  if "$php_binary" "$script_root/query.php" 127.0.0.1 "$dns_port" ready.test 127.0.0.1 \
      >/dev/null 2>&1 &&
     curl --noproxy '*' --fail --silent "http://127.0.0.1:$http_port/health" \
      >/dev/null 2>&1; then
    break
  fi
  sleep 0.05
done

nmcli connection add \
  type dummy \
  ifname "$resolver_interface" \
  con-name "$resolver_connection" \
  ipv4.method manual \
  ipv4.addresses 192.0.2.1/32 \
  ipv4.dns "dns+udp://127.0.0.1:$dns_port" \
  ipv4.dns-search '~test' \
  ipv4.never-default yes \
  ipv6.method disabled
route_active=true
nmcli connection up "$resolver_connection"

if [[ ${VERBOSE_RESOLVER_STATE:-false} == true ]]; then
  resolvectl status "$resolver_interface"
fi

if ! getent ahostsv4 anything.test | awk '$1 == "127.0.0.1" { found=1 } END { exit !found }'; then
  printf 'libc did not resolve anything.test through the route-only resolver.\n' >&2
  exit 1
fi
if ! getent ahostsv4 api.project.test | awk '$1 == "127.0.0.1" { found=1 } END { exit !found }'; then
  printf 'libc did not resolve nested .test aliases.\n' >&2
  exit 1
fi

body=$(curl --noproxy '*' --fail --silent "http://anything.test:$http_port/health")
if [[ "$body" != *'ok app-php-a'* ]]; then
  printf 'curl did not use wildcard .test resolution, got: %s\n' "$body" >&2
  exit 1
fi

public_during=$(getent ahostsv4 example.com | awk 'NR == 1 { print $1 }')
if [[ -z "$public_during" ]]; then
  printf 'Public DNS failed while the .test route was active.\n' >&2
  exit 1
fi

nmcli connection delete "$resolver_connection"
route_active=false

if getent ahostsv4 paddock-cleanup-check.test >/dev/null 2>&1; then
  printf '.test still resolves after reverting the loopback link.\n' >&2
  exit 1
fi

public_after=$(getent ahostsv4 example.com | awk 'NR == 1 { print $1 }')
if [[ -z "$public_after" ]]; then
  printf 'Public DNS failed after resolver cleanup.\n' >&2
  exit 1
fi

printf 'resolved integration passed public_before=%s public_during=%s public_after=%s\n' \
  "$public_before" "$public_during" "$public_after"
