#!/usr/bin/env bash

set -euo pipefail

probe_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
composer_path=${COMPOSER_PATH:-}
temporary_dir=$(mktemp -d)
fpm_pids=()

cleanup() {
  local pid

  for pid in "${fpm_pids[@]}"; do
    stop_process "$pid"
  done

  rm -rf -- "$temporary_dir"
}

stop_process() {
  local pid=$1

  if ! kill -0 "$pid" 2>/dev/null; then
    wait "$pid" 2>/dev/null || true
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

trap cleanup EXIT

if (( $# < 2 )); then
  printf 'Usage: %s <runtime-root> <runtime-root> [...]\n' "$0" >&2
  exit 2
fi

default_required_extensions='curl dom fileinfo filter intl mbstring openssl pdo session tokenizer xml zip'
read -r -a required_extensions <<<"${REQUIRED_EXTENSIONS:-$default_required_extensions}"

runtime_index=0

for runtime_root in "$@"; do
  runtime_index=$((runtime_index + 1))
  php_binary="$runtime_root/bin/php"
  fpm_binary="$runtime_root/bin/php-fpm"
  runtime_dir="$temporary_dir/runtime-$runtime_index"
  socket_path="$runtime_dir/php-fpm.sock"
  pid_path="$runtime_dir/php-fpm.pid"
  log_path="$runtime_dir/php-fpm.log"
  config_path="$runtime_dir/php-fpm.conf"

  if [[ ! -x "$php_binary" || ! -x "$fpm_binary" ]]; then
    printf 'Runtime must contain executable bin/php and bin/php-fpm: %s\n' "$runtime_root" >&2
    exit 1
  fi

  mkdir -p "$runtime_dir"
  sed \
    -e "s|@PID@|$pid_path|g" \
    -e "s|@LOG@|$log_path|g" \
    -e "s|@SOCKET@|$socket_path|g" \
    "$probe_root/fpm.conf.in" >"$config_path"

  version=$("$php_binary" -r 'echo PHP_VERSION;')
  fpm_version=$("$fpm_binary" -v | awk 'NR == 1 {print $2}')

  if [[ "$version" != "$fpm_version" ]]; then
    printf 'CLI/FPM version mismatch in %s: %s versus %s\n' "$runtime_root" "$version" "$fpm_version" >&2
    exit 1
  fi

  missing_extensions=()
  for extension in "${required_extensions[@]}"; do
    if ! "$php_binary" -r "exit(extension_loaded('$extension') ? 0 : 1);"; then
      missing_extensions+=("$extension")
    fi
  done

  if (( ${#missing_extensions[@]} > 0 )); then
    printf 'Runtime %s is missing required extensions: %s\n' \
      "$version" "${missing_extensions[*]}" >&2
    exit 1
  fi

  if [[ -n "$composer_path" ]]; then
    "$php_binary" "$composer_path" --version >/dev/null
  fi

  "$fpm_binary" -t -y "$config_path" >/dev/null
  "$fpm_binary" -y "$config_path" &
  fpm_pids+=("$!")

  for attempt in {1..50}; do
    if [[ -S "$socket_path" ]]; then
      break
    fi

    if [[ "$attempt" == 50 ]]; then
      printf 'FPM %s did not create %s.\n' "$version" "$socket_path" >&2
      sed -n '1,100p' "$log_path" >&2 || true
      exit 1
    fi

    sleep 0.05
  done

  printf 'ready php=%s socket=%s pid=%s\n' "$version" "$socket_path" "${fpm_pids[-1]}"
done

for pid in "${fpm_pids[@]}"; do
  if ! kill -0 "$pid" 2>/dev/null; then
    printf 'FPM process stopped unexpectedly: %s\n' "$pid" >&2
    exit 1
  fi
done

printf '%s runtimes passed simultaneous CLI/FPM checks.\n' "$#"
