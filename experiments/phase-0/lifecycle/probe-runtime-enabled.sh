#!/usr/bin/env bash

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  printf 'Run this probe with sudo.\n' >&2
  exit 2
fi

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
script_root="$repository_root/experiments/phase-0/lifecycle"
fixture_root="$repository_root/experiments/phase-0/fixtures"
php_tools="$repository_root/experiments/phase-0/php"
fpm_tools="$repository_root/experiments/phase-0/fpm"
desktop_user=${PADDOCK_USER:-${SUDO_USER:-}}
runtime_a=${RUNTIME_A:-/tmp/paddock-php-phase0/build-8.4/buildroot}
runtime_b=${RUNTIME_B:-/tmp/paddock-php-phase0/build-8.5/buildroot}
http_port=${HTTP_PORT:-18083}
admin_port=${ADMIN_PORT:-20193}
state_dir=$(mktemp -d /tmp/paddock-enabled.XXXXXX)
unit_root=/run/systemd/system
target=paddock-lifecycle.target
unit_a=paddock-lifecycle-php84.service
unit_b=paddock-lifecycle-php85.service
unit_caddy=paddock-lifecycle-caddy.service

if [[ -z "$desktop_user" ]]; then
  printf 'PADDOCK_USER or SUDO_USER is required.\n' >&2
  exit 2
fi
desktop_group=$(id -gn "$desktop_user")

cleanup() {
  systemctl disable --runtime "$target" >/dev/null 2>&1 || true
  systemctl stop "$target" >/dev/null 2>&1 || true
  rm -f -- \
    "$unit_root/$target" \
    "$unit_root/$unit_a" \
    "$unit_root/$unit_b" \
    "$unit_root/$unit_caddy" \
    /run/paddock-lifecycle-wait-socket
  systemctl daemon-reload >/dev/null 2>&1 || true
  rm -rf -- "$state_dir"
}
trap cleanup EXIT

chmod 700 "$state_dir"
mkdir -p "$state_dir/data" "$state_dir/config"

render_fpm_config() {
  local name=$1
  local socket=$2
  sed \
    -e "s|@PID@|$state_dir/$name.pid|g" \
    -e "s|@LOG@|$state_dir/$name.log|g" \
    -e "s|@SOCKET@|$socket|g" \
    "$php_tools/fpm.conf.in" >"$state_dir/$name.conf"
}

socket_a="$state_dir/php-8.4.sock"
socket_b="$state_dir/php-8.5.sock"
render_fpm_config php-8.4 "$socket_a"
render_fpm_config php-8.5 "$socket_b"

sed \
  -e "s|@ADMIN_PORT@|$admin_port|g" \
  -e "s|@HTTP_PORT@|$http_port|g" \
  -e "s|@APP_A_ROOT@|$fixture_root/app-php-a/public|g" \
  -e "s|@APP_B_ROOT@|$fixture_root/app-php-b/public|g" \
  -e "s|@SOCKET_A@|$socket_a|g" \
  -e "s|@SOCKET_B@|$socket_b|g" \
  -e "s|@ACCESS_A@|$state_dir/app-a.access.log|g" \
  -e "s|@ACCESS_B@|$state_dir/app-b.access.log|g" \
  -e "s|@GENERATION@|runtime-enabled|g" \
  "$fpm_tools/Caddyfile.in" >"$state_dir/Caddyfile"

render_fpm_unit() {
  local version=$1
  local runtime=$2
  local config=$3
  local socket=$4
  local output=$5
  sed \
    -e "s|@VERSION@|$version|g" \
    -e "s|@USER@|$desktop_user|g" \
    -e "s|@GROUP@|$desktop_group|g" \
    -e "s|@RUNTIME_COMMAND@|$php_tools/runtime-command.sh|g" \
    -e "s|@RUNTIME_ROOT@|$runtime|g" \
    -e "s|@STATE_DIR@|$state_dir|g" \
    -e "s|@FPM_CONFIG@|$config|g" \
    -e "s|@SOCKET@|$socket|g" \
    "$script_root/php-fpm.service.in" >"$output"
}

render_fpm_unit 8.4 "$runtime_a" "$state_dir/php-8.4.conf" "$socket_a" "$state_dir/$unit_a"
render_fpm_unit 8.5 "$runtime_b" "$state_dir/php-8.5.conf" "$socket_b" "$state_dir/$unit_b"
sed \
  -e "s|@USER@|$desktop_user|g" \
  -e "s|@GROUP@|$desktop_group|g" \
  -e "s|@STATE_DIR@|$state_dir|g" \
  -e "s|@CADDY_CONFIG@|$state_dir/Caddyfile|g" \
  -e "s|@ADMIN_PORT@|$admin_port|g" \
  "$script_root/caddy.service.in" >"$state_dir/$unit_caddy"

chown -R "$desktop_user:$desktop_group" "$state_dir"
install -o root -g root -m 0755 "$script_root/wait-for-socket.sh" /run/paddock-lifecycle-wait-socket
install -o root -g root -m 0644 "$script_root/paddock-lifecycle.target" "$unit_root/$target"
install -o root -g root -m 0644 "$state_dir/$unit_a" "$unit_root/$unit_a"
install -o root -g root -m 0644 "$state_dir/$unit_b" "$unit_root/$unit_b"
install -o root -g root -m 0644 "$state_dir/$unit_caddy" "$unit_root/$unit_caddy"
systemctl daemon-reload
systemd-analyze verify "$unit_root/$target" "$unit_root/$unit_a" "$unit_root/$unit_b" "$unit_root/$unit_caddy"

systemctl enable --runtime "$target"
[[ $(systemctl is-enabled "$target") == enabled-runtime ]]
systemctl start "$target"

status() {
  local host=$1
  curl --noproxy '*' --silent --output /dev/null --write-out '%{http_code}' \
    --resolve "$host:$http_port:127.0.0.1" "http://$host:$http_port/health"
}

for _ in {1..100}; do
  [[ $(status app-a.test) == 200 && $(status app-b.test) == 200 ]] && break
  sleep 0.05
done
[[ $(status app-a.test) == 200 && $(status app-b.test) == 200 ]]

systemctl stop "$unit_a"
[[ $(systemctl is-active "$unit_caddy") == active ]]
[[ $(status app-a.test) == 502 && $(status app-b.test) == 200 ]]
systemctl start "$unit_a"
[[ $(status app-a.test) == 200 && $(status app-b.test) == 200 ]]

systemctl restart "$unit_caddy"
[[ $(status app-a.test) == 200 && $(status app-b.test) == 200 ]]

systemctl stop "$target"
for unit in "$unit_a" "$unit_b" "$unit_caddy"; do
  [[ $(systemctl is-active "$unit" 2>/dev/null || true) != active ]]
done

systemctl disable --runtime "$target"
[[ $(systemctl is-enabled "$target" 2>/dev/null || true) == disabled ]]

printf 'runtime-enabled lifecycle passed enable=runtime target-ordering=yes stop-start=yes target-stop=yes disable=yes\n'
