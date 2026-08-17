#!/usr/bin/env bash

set -euo pipefail

script_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd "$script_root/../../.." && pwd)
php_84=${PHP_84:-}
php_85=${PHP_85:-}
composer=${COMPOSER_PATH:-"$HOME/.config/herd-lite/bin/composer"}
temporary_dir=$(mktemp -d)
server_a_pid=""
server_b_pid=""

cleanup() {
  for pid in "$server_a_pid" "$server_b_pid"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
  rm -rf -- "$temporary_dir"
}
trap cleanup EXIT

if [[ -z "$php_84" || -z "$php_85" ]]; then
  printf 'PHP_84 and PHP_85 are required.\n' >&2
  exit 2
fi
for executable in "$php_84" "$php_85" "$composer"; do
  [[ -f "$executable" ]] || {
    printf 'Required executable/file is missing: %s\n' "$executable" >&2
    exit 1
  }
done

project_a="$temporary_dir/Project A ünicode"
project_b="$temporary_dir/Project B with spaces"
outside="$temporary_dir/outside"
mkdir -p "$project_a/nested/deeper" "$project_b/nested/override" "$outside"
cp -a "$repository_root/experiments/phase-0/fixtures/." "$temporary_dir/fixtures/"
ln -s "$project_a" "$temporary_dir/symlink-to-a"

registry="$temporary_dir/registry.json"
sed \
  -e "s|@COMPOSER@|$composer|g" \
  -e "s|@PHP_84@|$php_84|g" \
  -e "s|@PHP_85@|$php_85|g" \
  -e "s|@PROJECT_A@|$project_a|g" \
  -e "s|@PROJECT_B@|$project_b|g" \
  "$script_root/registry.json.in" >"$registry"

dispatch() {
  PADDOCK_REGISTRY="$registry" "$script_root/dispatch.py" "$@"
}

version_for() {
  local directory=$1
  dispatch --cwd "$directory" php -- -r 'echo PHP_MAJOR_VERSION,".",PHP_MINOR_VERSION;' 
}

[[ $(version_for "$project_a") == 8.4 ]]
[[ $(version_for "$project_a/nested/deeper") == 8.4 ]]
[[ $(version_for "$project_b") == 8.5 ]]
[[ $(version_for "$project_b/nested") == 8.5 ]]
[[ $(version_for "$outside") == 8.5 ]]
[[ $(version_for "$temporary_dir/symlink-to-a/nested") == 8.4 ]]

sed 's|@PHP_VERSION@|8.4|g' "$script_root/project.json.in" \
  >"$project_b/nested/.paddock.json"
[[ $(version_for "$project_b/nested/override") == 8.4 ]]

PADDOCK_PASSTHROUGH=yes dispatch --cwd "$project_a" php -- \
  -r 'exit(getenv("PADDOCK_PASSTHROUGH") === "yes" ? 0 : 9);'

set +e
dispatch --cwd "$project_a" php -- -r 'exit(37);'
exit_status=$?
set -e
[[ $exit_status == 37 ]]

composer_output=$(dispatch --cwd "$project_a" composer -- --version 2>&1)
[[ "$composer_output" == *'PHP version 8.4.23'* ]]
composer_output=$(dispatch --cwd "$project_b" composer -- --version 2>&1)
[[ "$composer_output" == *'PHP version 8.5.8'* ]]

missing_project="$temporary_dir/missing-runtime"
mkdir "$missing_project"
sed 's|@PHP_VERSION@|9.9|g' "$script_root/project.json.in" \
  >"$missing_project/.paddock.json"
set +e
missing_output=$(dispatch --cwd "$missing_project" php -- -v 2>&1)
missing_status=$?
set -e
[[ $missing_status == 78 ]]
[[ "$missing_output" == *'PHP 9.9 selected by'* ]]
[[ "$missing_output" == *'paddock php install 9.9'* ]]

"$php_84" -S 127.0.0.1:18085 \
  -t "$temporary_dir/fixtures/app-php-a/public" \
  "$temporary_dir/fixtures/shared/router.php" \
  >"$temporary_dir/http-a.log" 2>&1 &
server_a_pid=$!
"$php_85" -S 127.0.0.1:18086 \
  -t "$temporary_dir/fixtures/app-php-b/public" \
  "$temporary_dir/fixtures/shared/router.php" \
  >"$temporary_dir/http-b.log" 2>&1 &
server_b_pid=$!

for _ in {1..80}; do
  if curl --noproxy '*' --fail --silent http://127.0.0.1:18085/health >/dev/null && \
     curl --noproxy '*' --fail --silent http://127.0.0.1:18086/health >/dev/null; then
    break
  fi
  sleep 0.05
done
http_a=$(curl --noproxy '*' --fail --silent http://127.0.0.1:18085/runtime)
http_b=$(curl --noproxy '*' --fail --silent http://127.0.0.1:18086/runtime)
[[ "$http_a" == *'"php_version": "8.4.23"'* ]]
[[ "$http_b" == *'"php_version": "8.5.8"'* ]]
[[ $(version_for "$project_a") == 8.4 ]]
[[ $(version_for "$project_b") == 8.5 ]]

printf 'CLI dispatch passed root+nested=yes default=8.5 symlink=canonical local-override=yes missing=actionable env=yes exit=37 composer=8.4,8.5 http-match=yes\n'
