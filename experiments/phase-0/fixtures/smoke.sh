#!/usr/bin/env bash

set -euo pipefail

fixture_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
php_binary=${PHP_BIN:-php}
port_a=${PORT_A:-18081}
port_b=${PORT_B:-18082}
temporary_dir=$(mktemp -d)
pid_a=""
pid_b=""

cleanup() {
  stop_process "$pid_a"
  stop_process "$pid_b"

  rm -rf -- "$temporary_dir"
}

stop_process() {
  local pid=$1

  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    return
  fi

  kill "$pid" 2>/dev/null || true

  for _ in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null || true
      return
    fi

    sleep 0.05
  done

  kill -KILL "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}

trap cleanup EXIT

if [[ ! -x "$(command -v "$php_binary" 2>/dev/null || true)" && ! -x "$php_binary" ]]; then
  printf 'PHP binary is not executable: %s\n' "$php_binary" >&2
  exit 1
fi

for php_file in \
  "$fixture_root/shared/front-controller.php" \
  "$fixture_root/shared/router.php" \
  "$fixture_root/app-php-a/public/index.php" \
  "$fixture_root/app-php-b/public/index.php"; do
  "$php_binary" -l "$php_file" >/dev/null
done

"$php_binary" -S "127.0.0.1:$port_a" \
  -t "$fixture_root/app-php-a/public" \
  "$fixture_root/shared/router.php" \
  >"$temporary_dir/app-a.log" 2>&1 &
pid_a=$!

"$php_binary" -S "127.0.0.1:$port_b" \
  -t "$fixture_root/app-php-b/public" \
  "$fixture_root/shared/router.php" \
  >"$temporary_dir/app-b.log" 2>&1 &
pid_b=$!

for attempt in {1..50}; do
  if curl --fail --silent "http://127.0.0.1:$port_a/health" >/dev/null && \
     curl --fail --silent "http://127.0.0.1:$port_b/health" >/dev/null; then
    break
  fi

  if [[ "$attempt" == 50 ]]; then
    printf 'Fixture servers did not become healthy.\n' >&2
    sed -n '1,80p' "$temporary_dir/app-a.log" >&2
    sed -n '1,80p' "$temporary_dir/app-b.log" >&2
    exit 1
  fi

  sleep 0.1
done

assert_body() {
  local url=$1
  local expected=$2
  local body
  body=$(curl --fail --silent "$url")

  if [[ "$body" != *"$expected"* ]]; then
    printf 'Expected %s to contain %q, got:\n%s\n' "$url" "$expected" "$body" >&2
    exit 1
  fi
}

assert_status() {
  local url=$1
  local expected=$2
  local actual
  actual=$(curl --silent --output /dev/null --write-out '%{http_code}' "$url")

  if [[ "$actual" != "$expected" ]]; then
    printf 'Expected %s from %s, got %s\n' "$expected" "$url" "$actual" >&2
    exit 1
  fi
}

assert_body "http://127.0.0.1:$port_a/health" 'ok app-php-a'
assert_body "http://127.0.0.1:$port_b/health" 'ok app-php-b'
assert_body "http://127.0.0.1:$port_a/runtime" '"fixture": "app-php-a"'
assert_body "http://127.0.0.1:$port_b/runtime" '"fixture": "app-php-b"'
assert_body "http://127.0.0.1:$port_a/nested/path" '"route": "/nested/path"'
assert_body "http://127.0.0.1:$port_b/nested/path" '"route": "/nested/path"'
assert_body "http://127.0.0.1:$port_a/fixture.txt" 'static app-php-a'
assert_body "http://127.0.0.1:$port_b/fixture.txt" 'static app-php-b'
assert_status "http://127.0.0.1:$port_a/failure" 500
assert_status "http://127.0.0.1:$port_b/failure" 500

if ! rg --quiet 'deliberate failure: app-php-a' "$temporary_dir/app-a.log"; then
  printf 'Deliberate app-php-a failure was not attributable in its log.\n' >&2
  exit 1
fi

if ! rg --quiet 'deliberate failure: app-php-b' "$temporary_dir/app-b.log"; then
  printf 'Deliberate app-php-b failure was not attributable in its log.\n' >&2
  exit 1
fi

printf 'Fixture smoke test passed with PHP %s.\n' "$("$php_binary" -r 'echo PHP_VERSION;')"
