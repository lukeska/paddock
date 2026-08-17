#!/usr/bin/env bash

set -euo pipefail

runtime_root=${1:-}
runtime_command=${2:-}

if [[ -z "$runtime_root" || -z "$runtime_command" ]]; then
  printf 'Usage: %s <runtime-root> <php|php-fpm> [arguments...]\n' "$0" >&2
  exit 2
fi
shift 2

runtime_root=$(cd "$runtime_root" && pwd -P)
runtime_binary="$runtime_root/bin/$runtime_command"
config_root="$runtime_root/etc"

case "$runtime_command" in
  php | php-fpm) ;;
  *)
    printf 'Unsupported runtime command: %s\n' "$runtime_command" >&2
    exit 2
    ;;
esac

if [[ ! -x "$runtime_binary" ]]; then
  printf 'Runtime binary is not executable: %s\n' "$runtime_binary" >&2
  exit 1
fi

if [[ ! -f "$config_root/php.ini" || ! -d "$config_root/conf.d" ]]; then
  printf 'Runtime configuration is incomplete: %s\n' "$config_root" >&2
  exit 1
fi

export PHPRC="$config_root/php.ini"
export PHP_INI_SCAN_DIR="$config_root/conf.d"

exec "$runtime_binary" \
  -d "extension_dir=$runtime_root/modules" \
  "$@"

