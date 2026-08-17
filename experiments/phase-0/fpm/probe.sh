#!/usr/bin/env bash

set -euo pipefail

script_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd "$script_root/../../.." && pwd)
fixture_root="$repository_root/experiments/phase-0/fixtures"
php_tools="$repository_root/experiments/phase-0/php"
runtime_a=${RUNTIME_A:-}
runtime_b=${RUNTIME_B:-}
http_port=${HTTP_PORT:-18080}
admin_port=${ADMIN_PORT:-20190}
temporary_dir=$(mktemp -d)
pid_a=""
pid_b=""
caddy_pid=""

if [[ -z "$runtime_a" || -z "$runtime_b" ]]; then
  printf 'Usage: RUNTIME_A=/path/to/php-8.4 RUNTIME_B=/path/to/php-8.5 %s\n' "$0" >&2
  exit 2
fi

runtime_a=$(cd "$runtime_a" && pwd -P)
runtime_b=$(cd "$runtime_b" && pwd -P)

stop_process() {
  local pid=$1

  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    return
  fi

  kill "$pid" 2>/dev/null || true
  for _ in {1..40}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null || true
      return
    fi
    sleep 0.05
  done

  kill -KILL "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}

cleanup() {
  stop_process "$caddy_pid"
  stop_process "$pid_a"
  stop_process "$pid_b"
  rm -rf -- "$temporary_dir"
}
trap cleanup EXIT

render_fpm_config() {
  local name=$1
  local socket=$2
  sed \
    -e "s|@PID@|$temporary_dir/$name.pid|g" \
    -e "s|@LOG@|$temporary_dir/$name.log|g" \
    -e "s|@SOCKET@|$socket|g" \
    "$php_tools/fpm.conf.in" >"$temporary_dir/$name.conf"
}

start_fpm() {
  local runtime=$1
  local name=$2
  local output_variable=$3
  local child_pid

  "$php_tools/runtime-command.sh" "$runtime" php-fpm \
    --nodaemonize \
    --fpm-config "$temporary_dir/$name.conf" \
    >"$temporary_dir/$name.stdout.log" 2>&1 &
  child_pid=$!
  printf -v "$output_variable" '%s' "$child_pid"
}

wait_for_socket() {
  local socket=$1
  local pid=$2

  for _ in {1..80}; do
    if [[ -S "$socket" ]]; then
      return
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      printf 'FPM exited before creating %s\n' "$socket" >&2
      return 1
    fi
    sleep 0.05
  done

  printf 'Timed out waiting for %s\n' "$socket" >&2
  return 1
}

request() {
  local host=$1
  local path=$2
  curl --noproxy '*' --silent --show-error \
    --resolve "$host:$http_port:127.0.0.1" \
    "http://$host:$http_port$path"
}

status() {
  local host=$1
  local path=$2
  curl --noproxy '*' --silent --output /dev/null --write-out '%{http_code}' \
    --resolve "$host:$http_port:127.0.0.1" \
    "http://$host:$http_port$path"
}

assert_contains() {
  local actual=$1
  local expected=$2
  if [[ "$actual" != *"$expected"* ]]; then
    printf 'Expected response to contain %q, got:\n%s\n' "$expected" "$actual" >&2
    exit 1
  fi
}

socket_a="$temporary_dir/php-8.4.sock"
socket_b="$temporary_dir/php-8.5.sock"
render_fpm_config php-8.4 "$socket_a"
render_fpm_config php-8.5 "$socket_b"

"$php_tools/runtime-command.sh" "$runtime_a" php-fpm \
  --test --fpm-config "$temporary_dir/php-8.4.conf"
"$php_tools/runtime-command.sh" "$runtime_b" php-fpm \
  --test --fpm-config "$temporary_dir/php-8.5.conf"

start_fpm "$runtime_a" php-8.4 pid_a
start_fpm "$runtime_b" php-8.5 pid_b
wait_for_socket "$socket_a" "$pid_a"
wait_for_socket "$socket_b" "$pid_b"

sed \
  -e "s|@ADMIN_PORT@|$admin_port|g" \
  -e "s|@HTTP_PORT@|$http_port|g" \
  -e "s|@APP_A_ROOT@|$fixture_root/app-php-a/public|g" \
  -e "s|@APP_B_ROOT@|$fixture_root/app-php-b/public|g" \
  -e "s|@SOCKET_A@|$socket_a|g" \
  -e "s|@SOCKET_B@|$socket_b|g" \
  -e "s|@ACCESS_A@|$temporary_dir/app-a.access.log|g" \
  -e "s|@ACCESS_B@|$temporary_dir/app-b.access.log|g" \
  -e "s|@GENERATION@|generation-1|g" \
  "$script_root/Caddyfile.in" >"$temporary_dir/Caddyfile"

caddy validate --config "$temporary_dir/Caddyfile" --adapter caddyfile
caddy run --config "$temporary_dir/Caddyfile" --adapter caddyfile \
  >"$temporary_dir/caddy.log" 2>&1 &
caddy_pid=$!

for attempt in {1..80}; do
  if [[ "$(status app-a.test /health)" == 200 &&
        "$(status app-b.test /health)" == 200 ]]; then
    break
  fi
  if ! kill -0 "$caddy_pid" 2>/dev/null || [[ "$attempt" == 80 ]]; then
    printf 'Caddy routes did not become healthy.\n' >&2
    sed -n '1,120p' "$temporary_dir/caddy.log" >&2
    exit 1
  fi
  sleep 0.05
done

assert_contains "$(request app-a.test /runtime)" '"php_version": "8.4.23"'
assert_contains "$(request app-b.test /runtime)" '"php_version": "8.5.8"'
assert_contains "$(request app-a.test /nested/path)" '"route": "/nested/path"'
assert_contains "$(request app-b.test /nested/path)" '"route": "/nested/path"'
assert_contains "$(request app-a.test /fixture.txt)" 'static app-php-a'
assert_contains "$(request app-b.test /fixture.txt)" 'static app-php-b'
assert_contains "$(request app-a.test /runtime)" '"forwarded_proto": "http"'
assert_contains "$(curl --noproxy '*' --silent --dump-header - --output /dev/null \
  --resolve "app-a.test:$http_port:127.0.0.1" \
  "http://app-a.test:$http_port/health")" 'X-Paddock-Config: generation-1'

rss_a=$(ps -o rss= -p "$pid_a" | awk '{ total += $1 } END { print total + 0 }')
rss_b=$(ps -o rss= -p "$pid_b" | awk '{ total += $1 } END { print total + 0 }')

stop_process "$pid_a"
pid_a=""

if [[ "$(status app-b.test /health)" != 200 ]]; then
  printf 'PHP 8.5 route failed while PHP 8.4 was stopped.\n' >&2
  exit 1
fi
if [[ "$(status app-a.test /health)" != 502 ]]; then
  printf 'Expected a 502 for the stopped PHP 8.4 socket.\n' >&2
  exit 1
fi

start_fpm "$runtime_a" php-8.4 pid_a
wait_for_socket "$socket_a" "$pid_a"
if [[ "$(status app-a.test /health)" != 200 ||
      "$(status app-b.test /health)" != 200 ]]; then
  printf 'Routes did not recover after the targeted PHP 8.4 restart.\n' >&2
  exit 1
fi

if ! rg --quiet 'php-8\.4\.sock|dial unix' "$temporary_dir/caddy.log"; then
  printf 'Caddy log did not identify the failed PHP 8.4 socket.\n' >&2
  exit 1
fi
if ! rg --quiet 'app-a\.test' "$temporary_dir/app-a.access.log" ||
   ! rg --quiet 'app-b\.test' "$temporary_dir/app-b.access.log"; then
  printf 'Per-site access logs were not attributable.\n' >&2
  exit 1
fi

sed 's/generation-1/generation-2/g' \
  "$temporary_dir/Caddyfile" >"$temporary_dir/Caddyfile.next"
caddy validate --config "$temporary_dir/Caddyfile.next" --adapter caddyfile

(
  for _ in {1..30}; do
    if [[ "$(status app-b.test /health)" != 200 ]]; then
      exit 1
    fi
  done
) &
traffic_pid=$!

caddy reload \
  --config "$temporary_dir/Caddyfile.next" \
  --adapter caddyfile \
  --address "127.0.0.1:$admin_port"
wait "$traffic_pid"

assert_contains "$(curl --noproxy '*' --silent --dump-header - --output /dev/null \
  --resolve "app-b.test:$http_port:127.0.0.1" \
  "http://app-b.test:$http_port/health")" 'X-Paddock-Config: generation-2'

sed 's/php_fastcgi/invalid_paddock_directive/' \
  "$temporary_dir/Caddyfile.next" >"$temporary_dir/Caddyfile.invalid"
if caddy validate --config "$temporary_dir/Caddyfile.invalid" --adapter caddyfile \
  >"$temporary_dir/invalid-validate.log" 2>&1; then
  printf 'Invalid Caddy configuration unexpectedly validated.\n' >&2
  exit 1
fi
if caddy reload \
  --config "$temporary_dir/Caddyfile.invalid" \
  --adapter caddyfile \
  --address "127.0.0.1:$admin_port" \
  >"$temporary_dir/invalid-reload.log" 2>&1; then
  printf 'Invalid Caddy configuration unexpectedly reloaded.\n' >&2
  exit 1
fi

if [[ "$(status app-a.test /health)" != 200 ||
      "$(status app-b.test /health)" != 200 ]]; then
  printf 'Healthy routes were disturbed by the rejected configuration.\n' >&2
  exit 1
fi
assert_contains "$(curl --noproxy '*' --silent --dump-header - --output /dev/null \
  --resolve "app-a.test:$http_port:127.0.0.1" \
  "http://app-a.test:$http_port/health")" 'X-Paddock-Config: generation-2'

printf 'FPM/Caddy passed: app-a=8.4.23 app-b=8.5.8 port=%s master_rss_kib=%s,%s reload=generation-2 invalid=rejected\n' \
  "$http_port" "$rss_a" "$rss_b"
