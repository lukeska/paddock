#!/usr/bin/env bash

set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
fixture_root="$repository_root/experiments/phase-0/fixtures"
php_tools="$repository_root/experiments/phase-0/php"
fpm_tools="$repository_root/experiments/phase-0/fpm"
runtime_a=${RUNTIME_A:-/tmp/paddock-php-phase0/build-8.4/buildroot}
runtime_b=${RUNTIME_B:-/tmp/paddock-php-phase0/build-8.5/buildroot}
http_port=${HTTP_PORT:-18082}
admin_port=${ADMIN_PORT:-20192}
desktop_user=${PADDOCK_USER:-${SUDO_USER:-$USER}}
desktop_group=$(id -gn "$desktop_user")
probe_dir=$(mktemp -d /tmp/paddock-lifecycle.XXXXXX)
unit_a=paddock-lifecycle-php84
unit_b=paddock-lifecycle-php85
unit_caddy=paddock-lifecycle-caddy

cleanup() {
  systemctl stop "$unit_caddy.service" "$unit_a.service" "$unit_b.service" \
    >/dev/null 2>&1 || true
  systemctl reset-failed "$unit_caddy.service" "$unit_a.service" "$unit_b.service" \
    >/dev/null 2>&1 || true
  systemctl daemon-reload >/dev/null 2>&1 || true
  rm -rf -- "$probe_dir"
}
trap cleanup EXIT

chmod 700 "$probe_dir"
mkdir -p "$probe_dir/data" "$probe_dir/config"

render_fpm() {
  local name=$1
  local socket=$2
  sed \
    -e "s|@PID@|$probe_dir/$name.pid|g" \
    -e "s|@LOG@|$probe_dir/$name.log|g" \
    -e "s|@SOCKET@|$socket|g" \
    "$php_tools/fpm.conf.in" >"$probe_dir/$name.conf"
}

socket_a="$probe_dir/php-8.4.sock"
socket_b="$probe_dir/php-8.5.sock"
render_fpm php-8.4 "$socket_a"
render_fpm php-8.5 "$socket_b"

sed \
  -e "s|@ADMIN_PORT@|$admin_port|g" \
  -e "s|@HTTP_PORT@|$http_port|g" \
  -e "s|@APP_A_ROOT@|$fixture_root/app-php-a/public|g" \
  -e "s|@APP_B_ROOT@|$fixture_root/app-php-b/public|g" \
  -e "s|@SOCKET_A@|$socket_a|g" \
  -e "s|@SOCKET_B@|$socket_b|g" \
  -e "s|@ACCESS_A@|$probe_dir/app-a.access.log|g" \
  -e "s|@ACCESS_B@|$probe_dir/app-b.access.log|g" \
  -e "s|@GENERATION@|lifecycle|g" \
  "$fpm_tools/Caddyfile.in" >"$probe_dir/Caddyfile"

chown -R "$desktop_user:$desktop_group" "$probe_dir"

start_fpm_unit() {
  local unit=$1
  local runtime=$2
  local config=$3
  systemd-run \
    --unit="$unit" \
    --uid="$desktop_user" \
    --property=Restart=on-failure \
    --property=RestartSec=200ms \
    --property=StartLimitIntervalSec=10s \
    --property=StartLimitBurst=3 \
    --property=NoNewPrivileges=yes \
    --property=PrivateDevices=yes \
    --property=ProtectSystem=strict \
    --property=ProtectHome=read-only \
    --property="ReadWritePaths=$probe_dir" \
    "$php_tools/runtime-command.sh" "$runtime" php-fpm \
      -d "opcache.lockfile_path=$probe_dir" \
      --nodaemonize --fpm-config "$config" >/dev/null
}

wait_for_socket() {
  local socket=$1
  for _ in {1..100}; do
    [[ -S "$socket" ]] && return
    sleep 0.05
  done
  printf 'Timed out waiting for %s\n' "$socket" >&2
  exit 1
}

status() {
  local host=$1
  curl --noproxy '*' --silent --output /dev/null --write-out '%{http_code}' \
    --resolve "$host:$http_port:127.0.0.1" "http://$host:$http_port/health"
}

start_fpm_unit "$unit_a" "$runtime_a" "$probe_dir/php-8.4.conf"
start_fpm_unit "$unit_b" "$runtime_b" "$probe_dir/php-8.5.conf"
wait_for_socket "$socket_a"
wait_for_socket "$socket_b"

runuser --user "$desktop_user" -- \
  env XDG_DATA_HOME="$probe_dir/data" XDG_CONFIG_HOME="$probe_dir/config" \
  /usr/bin/caddy validate --config "$probe_dir/Caddyfile" --adapter caddyfile \
  >/dev/null
systemd-run \
  --unit="$unit_caddy" \
  --uid="$desktop_user" \
  --setenv="XDG_DATA_HOME=$probe_dir/data" \
  --setenv="XDG_CONFIG_HOME=$probe_dir/config" \
  --property=Restart=on-failure \
  --property=RestartSec=500ms \
  --property=StartLimitIntervalSec=10s \
  --property=StartLimitBurst=3 \
  --property=NoNewPrivileges=yes \
  --property=PrivateDevices=yes \
  --property=ProtectSystem=strict \
  --property=ProtectHome=read-only \
  --property="ReadWritePaths=$probe_dir" \
  /usr/bin/caddy run --config "$probe_dir/Caddyfile" --adapter caddyfile >/dev/null

for _ in {1..100}; do
  [[ $(status app-a.test) == 200 && $(status app-b.test) == 200 ]] && break
  sleep 0.05
done
if [[ $(status app-a.test) != 200 || $(status app-b.test) != 200 ]]; then
  printf 'Caddy routes did not become healthy before the crash test.\n' >&2
  exit 1
fi

pid_a_before=$(systemctl show -p MainPID --value "$unit_a.service")
kill -KILL "$pid_a_before"

for _ in {1..100}; do
  pid_a_after=$(systemctl show -p MainPID --value "$unit_a.service")
  if [[ "$pid_a_after" != 0 && "$pid_a_after" != "$pid_a_before" && \
        $(status app-a.test) == 200 ]]; then
    break
  fi
  [[ $(status app-b.test) == 200 ]] || {
    printf 'PHP 8.5 was disturbed by the PHP 8.4 crash.\n' >&2
    exit 1
  }
  sleep 0.05
done

restarts_a=$(systemctl show -p NRestarts --value "$unit_a.service")
if (( restarts_a < 1 )); then
  printf 'PHP 8.4 did not record an automatic restart.\n' >&2
  exit 1
fi

systemctl stop "$unit_b.service"
[[ $(status app-a.test) == 200 ]] || exit 1
[[ $(status app-b.test) == 502 ]] || exit 1
start_fpm_unit "$unit_b" "$runtime_b" "$probe_dir/php-8.5.conf"
wait_for_socket "$socket_b"
[[ $(status app-a.test) == 200 && $(status app-b.test) == 200 ]] || exit 1

if ! journalctl -u "$unit_a.service" --since '-2 minutes' --no-pager | \
    rg -q 'code=killed.*status=9/KILL|status=9/KILL'; then
  printf 'journald did not expose the PHP 8.4 crash.\n' >&2
  exit 1
fi

systemctl stop "$unit_a.service"
sleep 0.3
[[ $(systemctl is-active "$unit_a.service" 2>/dev/null || true) != active ]] || {
  printf 'Clean stop unexpectedly restarted PHP 8.4.\n' >&2
  exit 1
}
[[ $(status app-b.test) == 200 ]] || exit 1

printf 'transient lifecycle passed php84_restarts=%s php85_independent=yes journald=visible clean_stop=no-restart\n' \
  "$restarts_a"
