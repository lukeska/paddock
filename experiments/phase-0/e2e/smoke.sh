#!/usr/bin/env bash

set -euo pipefail

state_dir=${PADDOCK_E2E_STATE:-"$HOME/.local/share/paddock/e2e-probe"}
registry="$state_dir/registry.json"
dispatch="$state_dir/dispatch.py"

[[ -f "$registry" && -x "$dispatch" ]] || { printf 'E2E state is not installed.\n' >&2; exit 1; }
for unit in dns dns-route php84 php85 caddy; do
  systemctl is-active --quiet "paddock-e2e-$unit.service"
done

for host in app-a.test app-b.test; do
  getent ahostsv4 "$host" | awk '$1 == "127.0.0.1" { found=1 } END { exit !found }'
  redirect=$(curl --silent --show-error --output /dev/null --write-out '%{http_code} %{redirect_url}' "http://$host/")
  [[ "$redirect" == "301 https://$host/" ]] || { printf 'Unexpected redirect: %s\n' "$redirect" >&2; exit 1; }
done

assert_body() {
  local url=$1 expected=$2 actual
  actual=$(curl --fail --silent --show-error "$url")
  [[ "$actual" == "$expected" ]] || { printf '%s: expected %q, got %q\n' "$url" "$expected" "$actual" >&2; exit 1; }
}
assert_body https://app-a.test/fixture.txt 'static app-php-a'
assert_body https://app-b.test/fixture.txt 'static app-php-b'
body_a=$(curl --fail --silent --show-error https://app-a.test/nested/path)
body_b=$(curl --fail --silent --show-error https://app-b.test/nested/path)
grep -Fq '"fixture": "app-php-a"' <<<"$body_a"
grep -Fq '"route": "/nested/path"' <<<"$body_a"
grep -Fq '"fixture": "app-php-b"' <<<"$body_b"
grep -Fq '"route": "/nested/path"' <<<"$body_b"
runtime_a=$(curl --fail --silent --show-error https://app-a.test/runtime)
runtime_b=$(curl --fail --silent --show-error https://app-b.test/runtime)
grep -Eq '"php_version": "8\.4\.' <<<"$runtime_a"
grep -Eq '"php_version": "8\.5\.' <<<"$runtime_b"

status=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' https://app-a.test/failure)
[[ "$status" == 500 ]]
grep -Fq 'Paddock fixture deliberate failure: app-php-a' "$state_dir/log/php84.log"

export PADDOCK_REGISTRY="$registry"
php_a=$($dispatch --cwd "$state_dir/fixtures/app-php-a" php -- -r 'echo PHP_MAJOR_VERSION,".",PHP_MINOR_VERSION;')
php_b=$($dispatch --cwd "$state_dir/fixtures/app-php-b" php -- -r 'echo PHP_MAJOR_VERSION,".",PHP_MINOR_VERSION;')
[[ "$php_a" == 8.4 && "$php_b" == 8.5 ]]
$dispatch --cwd "$state_dir/fixtures/app-php-a" composer -- --version | grep -Eq 'PHP version 8\.4|Composer version'
$dispatch --cwd "$state_dir/fixtures/app-php-b" composer -- --version | grep -Eq 'PHP version 8\.5|Composer version'

printf 'combined E2E passed boot_id=%s setup_boot_id=%s\n' \
  "$(cat /proc/sys/kernel/random/boot_id)" "$(cat "$state_dir/setup-boot-id")"
