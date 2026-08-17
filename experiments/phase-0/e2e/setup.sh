#!/usr/bin/env bash

set -euo pipefail

if (( EUID != 0 )); then
  printf 'Run with sudo: sudo PADDOCK_USER="$USER" RUNTIME_SOURCE=... %s\n' "$0" >&2
  exit 2
fi

desktop_user=${PADDOCK_USER:-${SUDO_USER:-}}
runtime_source=${RUNTIME_SOURCE:-}
if [[ -z "$desktop_user" || "$desktop_user" == root || -z "$runtime_source" ]]; then
  printf 'PADDOCK_USER and RUNTIME_SOURCE are required.\n' >&2
  exit 2
fi

desktop_home=$(getent passwd "$desktop_user" | cut -d: -f6)
desktop_group=$(id -gn "$desktop_user")
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
phase_dir=$(cd -- "$script_dir/.." && pwd)
state_dir="$desktop_home/.local/share/paddock/e2e-probe"
unit_dir=/etc/systemd/system
connection=paddock-e2e-dns
units=(paddock-e2e.target paddock-e2e-dns.service paddock-e2e-dns-route.service paddock-e2e-php84.service paddock-e2e-php85.service paddock-e2e-caddy.service)

for version in 8.4 8.5; do
  [[ -x "$runtime_source/$version/php" && -x "$runtime_source/$version/php-fpm" ]] || {
    printf 'Missing PHP %s runtime in %s\n' "$version" "$runtime_source" >&2
    exit 2
  }
done
[[ -f "$desktop_home/.config/herd-lite/bin/composer" ]] || {
  printf 'Composer fixture not found in %s/.config/herd-lite/bin/composer\n' "$desktop_home" >&2
  exit 2
}
[[ -d "$desktop_home/.local/share/paddock/pki" ]] || {
  printf 'Paddock CA not found; complete the TLS probe first.\n' >&2
  exit 2
}
[[ ! -e "$state_dir" ]] || { printf 'Refusing to replace %s\n' "$state_dir" >&2; exit 1; }
for unit in "${units[@]}"; do
  [[ ! -e "$unit_dir/$unit" ]] || { printf 'Refusing to replace %s/%s\n' "$unit_dir" "$unit" >&2; exit 1; }
done
if nmcli -t -f NAME connection show | grep -Fxq "$connection"; then
  printf 'Refusing to replace NetworkManager connection %s\n' "$connection" >&2
  exit 1
fi

installed=no
cleanup_failed_setup() {
  [[ "$installed" == yes ]] && return
  systemctl disable --now paddock-e2e.target >/dev/null 2>&1 || true
  nmcli connection delete "$connection" >/dev/null 2>&1 || true
  for unit in "${units[@]}"; do rm -f -- "$unit_dir/$unit"; done
  rm -f -- /etc/paddock-e2e-dnsmasq.conf /usr/local/lib/paddock-e2e-wait-socket /usr/local/lib/paddock-e2e-check-ports
  rm -rf -- "$state_dir"
  systemctl daemon-reload >/dev/null 2>&1 || true
}
trap cleanup_failed_setup EXIT

install -d -m 0755 -o "$desktop_user" -g "$desktop_group" "$state_dir"
install -d -m 0755 -o "$desktop_user" -g "$desktop_group" "$state_dir"/{runtimes,fixtures,run,log,tls,caddy-data,caddy-config}
cp -a -- "$runtime_source/8.4" "$state_dir/runtimes/8.4"
cp -a -- "$runtime_source/8.5" "$state_dir/runtimes/8.5"
cp -a -- "$phase_dir/fixtures/." "$state_dir/fixtures/"
install -m 0755 -o "$desktop_user" -g "$desktop_group" "$phase_dir/cli/dispatch.py" "$state_dir/dispatch.py"
install -m 0644 -o "$desktop_user" -g "$desktop_group" "$desktop_home/.config/herd-lite/bin/composer" "$state_dir/composer.phar"
chown -R "$desktop_user:$desktop_group" "$state_dir"

printf '{"php":"8.4"}\n' >"$state_dir/fixtures/app-php-a/.paddock.json"
printf '{"php":"8.5"}\n' >"$state_dir/fixtures/app-php-b/.paddock.json"
chown "$desktop_user:$desktop_group" "$state_dir/fixtures/app-php-a/.paddock.json" "$state_dir/fixtures/app-php-b/.paddock.json"

render_fpm() {
  local version=$1 suffix=$2
  sed -e "s|@PID@|$state_dir/run/php$suffix.pid|g" \
      -e "s|@LOG@|$state_dir/log/php$suffix.log|g" \
      -e "s|@SOCKET@|$state_dir/run/php$suffix.sock|g" \
      "$phase_dir/php/fpm.conf.in" >"$state_dir/php$suffix-fpm.conf"
  chown "$desktop_user:$desktop_group" "$state_dir/php$suffix-fpm.conf"
}
render_fpm 8.4 84
render_fpm 8.5 85

runuser --user "$desktop_user" -- env CAROOT="$desktop_home/.local/share/paddock/pki" \
  mkcert -cert-file "$state_dir/tls/sites.pem" -key-file "$state_dir/tls/sites-key.pem" app-a.test app-b.test >/dev/null

sed -e "s|@CERTIFICATE@|$state_dir/tls/sites.pem|g" \
    -e "s|@PRIVATE_KEY@|$state_dir/tls/sites-key.pem|g" \
    -e "s|@APP_A_ROOT@|$state_dir/fixtures/app-php-a/public|g" \
    -e "s|@APP_B_ROOT@|$state_dir/fixtures/app-php-b/public|g" \
    -e "s|@ACCESS_A@|$state_dir/log/access-a.log|g" \
    -e "s|@ACCESS_B@|$state_dir/log/access-b.log|g" \
    -e "s|@SOCKET_A@|$state_dir/run/php84.sock|g" \
    -e "s|@SOCKET_B@|$state_dir/run/php85.sock|g" \
    "$script_dir/Caddyfile.in" >"$state_dir/Caddyfile"

sed -e "s|@COMPOSER@|$state_dir/composer.phar|g" \
    -e "s|@PHP_84@|$state_dir/runtimes/8.4/php|g" \
    -e "s|@PHP_85@|$state_dir/runtimes/8.5/php|g" \
    -e "s|@PROJECT_A@|$state_dir/fixtures/app-php-a|g" \
    -e "s|@PROJECT_B@|$state_dir/fixtures/app-php-b|g" \
    "$phase_dir/cli/registry.json.in" >"$state_dir/registry.json"
chown -R "$desktop_user:$desktop_group" "$state_dir"

install -m 0755 "$phase_dir/lifecycle/wait-for-socket.sh" /usr/local/lib/paddock-e2e-wait-socket
install -m 0755 "$phase_dir/ports/check-conflicts.sh" /usr/local/lib/paddock-e2e-check-ports
install -m 0644 "$script_dir/dnsmasq.conf" /etc/paddock-e2e-dnsmasq.conf

nmcli connection add type dummy ifname paddock-e2e0 con-name "$connection" \
  ipv4.method manual ipv4.addresses 192.0.2.10/32 ipv4.dns 127.0.0.1 \
  ipv4.dns-search '~test' ipv4.never-default yes ipv6.method disabled \
  connection.autoconnect yes >/dev/null

render_unit() {
  local input=$1 output=$2
  sed -e "s|@USER@|$desktop_user|g" \
      -e "s|@GROUP@|$desktop_group|g" \
      -e "s|@STATE_DIR@|$state_dir|g" \
      -e "s|@CADDY_CONFIG@|$state_dir/Caddyfile|g" \
      "$input" >"$output"
}
install -m 0644 "$script_dir/paddock-e2e.target" "$unit_dir/paddock-e2e.target"
install -m 0644 "$script_dir/dns.service" "$unit_dir/paddock-e2e-dns.service"
install -m 0644 "$script_dir/dns-route.service" "$unit_dir/paddock-e2e-dns-route.service"
render_unit "$script_dir/caddy.service.in" "$unit_dir/paddock-e2e-caddy.service"
for spec in '8.4 84' '8.5 85'; do
  read -r version suffix <<<"$spec"
  sed -e "s|@VERSION@|$version|g" \
      -e "s|@USER@|$desktop_user|g" \
      -e "s|@GROUP@|$desktop_group|g" \
      -e "s|@PHP_FPM@|$state_dir/runtimes/$version/php-fpm|g" \
      -e "s|@STATE_DIR@|$state_dir|g" \
      -e "s|@FPM_CONFIG@|$state_dir/php$suffix-fpm.conf|g" \
      -e "s|@SOCKET@|$state_dir/run/php$suffix.sock|g" \
      "$script_dir/php-fpm.service.in" >"$unit_dir/paddock-e2e-php$suffix.service"
done

printf '%s\n' "$(cat /proc/sys/kernel/random/boot_id)" >"$state_dir/setup-boot-id"
chown "$desktop_user:$desktop_group" "$state_dir/setup-boot-id"
systemctl daemon-reload
systemd-analyze verify "${units[@]/#/$unit_dir/}"
systemctl enable --now paddock-e2e.target
installed=yes
printf 'E2E setup healthy state=%s boot_id=%s\n' "$state_dir" "$(cat "$state_dir/setup-boot-id")"
