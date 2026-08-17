#!/usr/bin/env bash

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  printf 'Run this setup with sudo.\n' >&2
  exit 2
fi

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
script_root="$repository_root/experiments/phase-0/lifecycle"
php_tools="$repository_root/experiments/phase-0/php"
fpm_tools="$repository_root/experiments/phase-0/fpm"
desktop_user=${PADDOCK_USER:-${SUDO_USER:-}}
source_runtime_a=${RUNTIME_A:-/tmp/paddock-php-phase0/build-8.4/buildroot}
source_runtime_b=${RUNTIME_B:-/tmp/paddock-php-phase0/build-8.5/buildroot}
http_port=${HTTP_PORT:-18084}
admin_port=${ADMIN_PORT:-20194}
target=paddock-reboot.target
unit_a=paddock-reboot-php84.service
unit_b=paddock-reboot-php85.service
unit_caddy=paddock-reboot-caddy.service
unit_root=/etc/systemd/system
helper=/usr/local/lib/paddock-reboot-wait-for-socket
setup_complete=false

if [[ -z "$desktop_user" ]]; then
  printf 'PADDOCK_USER or SUDO_USER is required.\n' >&2
  exit 2
fi
desktop_group=$(id -gn "$desktop_user")
desktop_home=$(getent passwd "$desktop_user" | cut -d: -f6)
state_dir="$desktop_home/.local/share/paddock/reboot-probe"
runtime_a="$state_dir/runtimes/8.4"
runtime_b="$state_dir/runtimes/8.5"

cleanup_failed_setup() {
  [[ "$setup_complete" == true ]] && return
  systemctl disable --now "$target" >/dev/null 2>&1 || true
  rm -f -- "$unit_root/$target" "$unit_root/$unit_a" "$unit_root/$unit_b" \
    "$unit_root/$unit_caddy" "$helper"
  systemctl daemon-reload >/dev/null 2>&1 || true
  rm -rf -- "$state_dir"
}
trap cleanup_failed_setup EXIT

for path in "$unit_root/$target" "$unit_root/$unit_a" "$unit_root/$unit_b" \
  "$unit_root/$unit_caddy" "$helper" "$state_dir"; do
  if [[ -e "$path" ]]; then
    printf 'Refusing to overwrite existing reboot-probe state: %s\n' "$path" >&2
    exit 1
  fi
done
for runtime in "$source_runtime_a" "$source_runtime_b"; do
  [[ -x "$runtime/bin/php-fpm" ]] || {
    printf 'Missing source runtime: %s\n' "$runtime" >&2
    exit 1
  }
done

install -d -o "$desktop_user" -g "$desktop_group" -m 0700 "$state_dir"
install -d -o "$desktop_user" -g "$desktop_group" -m 0755 \
  "$state_dir/runtimes" "$state_dir/config" "$state_dir/data" "$state_dir/fixtures"
cp -a "$source_runtime_a" "$runtime_a"
cp -a "$source_runtime_b" "$runtime_b"
cp -a "$repository_root/experiments/phase-0/fixtures/." "$state_dir/fixtures/"

socket_a="$state_dir/php-8.4.sock"
socket_b="$state_dir/php-8.5.sock"
for version in 8.4 8.5; do
  sed \
    -e "s|@PID@|$state_dir/php-$version.pid|g" \
    -e "s|@LOG@|$state_dir/php-$version.log|g" \
    -e "s|@SOCKET@|$state_dir/php-$version.sock|g" \
    "$php_tools/fpm.conf.in" >"$state_dir/php-$version.conf"
done

sed \
  -e "s|@ADMIN_PORT@|$admin_port|g" \
  -e "s|@HTTP_PORT@|$http_port|g" \
  -e "s|@APP_A_ROOT@|$state_dir/fixtures/app-php-a/public|g" \
  -e "s|@APP_B_ROOT@|$state_dir/fixtures/app-php-b/public|g" \
  -e "s|@SOCKET_A@|$socket_a|g" \
  -e "s|@SOCKET_B@|$socket_b|g" \
  -e "s|@ACCESS_A@|$state_dir/app-a.access.log|g" \
  -e "s|@ACCESS_B@|$state_dir/app-b.access.log|g" \
  -e "s|@GENERATION@|reboot-probe|g" \
  "$fpm_tools/Caddyfile.in" >"$state_dir/Caddyfile"

render_fpm_unit() {
  local version=$1 runtime=$2 config=$3 socket=$4 output=$5
  sed \
    -e 's|paddock-lifecycle|paddock-reboot|g' \
    -e "s|/run/paddock-reboot-wait-socket|$helper|g" \
    -e "s|@VERSION@|$version|g" \
    -e "s|@USER@|$desktop_user|g" \
    -e "s|@GROUP@|$desktop_group|g" \
    -e "s|@RUNTIME_COMMAND@|$state_dir/runtime-command.sh|g" \
    -e "s|@RUNTIME_ROOT@|$runtime|g" \
    -e "s|@STATE_DIR@|$state_dir|g" \
    -e "s|@FPM_CONFIG@|$config|g" \
    -e "s|@SOCKET@|$socket|g" \
    "$script_root/php-fpm.service.in" >"$output"
}

cp "$php_tools/runtime-command.sh" "$state_dir/runtime-command.sh"
render_fpm_unit 8.4 "$runtime_a" "$state_dir/php-8.4.conf" "$socket_a" "$state_dir/$unit_a"
render_fpm_unit 8.5 "$runtime_b" "$state_dir/php-8.5.conf" "$socket_b" "$state_dir/$unit_b"
sed \
  -e 's|paddock-lifecycle|paddock-reboot|g' \
  -e "s|@USER@|$desktop_user|g" \
  -e "s|@GROUP@|$desktop_group|g" \
  -e "s|@STATE_DIR@|$state_dir|g" \
  -e "s|@CADDY_CONFIG@|$state_dir/Caddyfile|g" \
  -e "s|@ADMIN_PORT@|$admin_port|g" \
  "$script_root/caddy.service.in" >"$state_dir/$unit_caddy"
sed 's|paddock-lifecycle|paddock-reboot|g' \
  "$script_root/paddock-lifecycle.target" >"$state_dir/$target"

cp /proc/sys/kernel/random/boot_id "$state_dir/boot-id-before"
chown -R "$desktop_user:$desktop_group" "$state_dir"
chmod 0700 "$state_dir"
chmod 0755 "$state_dir/runtime-command.sh"

install -o root -g root -m 0755 "$script_root/wait-for-socket.sh" "$helper"
install -o root -g root -m 0644 "$state_dir/$target" "$unit_root/$target"
install -o root -g root -m 0644 "$state_dir/$unit_a" "$unit_root/$unit_a"
install -o root -g root -m 0644 "$state_dir/$unit_b" "$unit_root/$unit_b"
install -o root -g root -m 0644 "$state_dir/$unit_caddy" "$unit_root/$unit_caddy"

systemctl daemon-reload
systemd-analyze verify "$unit_root/$target" "$unit_root/$unit_a" "$unit_root/$unit_b" "$unit_root/$unit_caddy"
systemctl enable --now "$target"

for _ in {1..100}; do
  if curl --noproxy '*' --fail --silent \
      --resolve "app-a.test:$http_port:127.0.0.1" \
      "http://app-a.test:$http_port/health" >/dev/null && \
     curl --noproxy '*' --fail --silent \
      --resolve "app-b.test:$http_port:127.0.0.1" \
      "http://app-b.test:$http_port/health" >/dev/null; then
    break
  fi
  sleep 0.05
done
curl --noproxy '*' --fail --silent --resolve "app-a.test:$http_port:127.0.0.1" \
  "http://app-a.test:$http_port/health" >/dev/null
curl --noproxy '*' --fail --silent --resolve "app-b.test:$http_port:127.0.0.1" \
  "http://app-b.test:$http_port/health" >/dev/null

setup_complete=true
printf 'reboot probe installed and healthy boot_id=%s port=%s\n' \
  "$(cat "$state_dir/boot-id-before")" "$http_port"
