#!/usr/bin/env bash

set -euo pipefail

script_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

if (( $# < 1 )); then
  printf 'Usage: %s <runtime-root> [runtime-root ...]\n' "$0" >&2
  exit 2
fi

for runtime_root in "$@"; do
  runtime_root=$(cd "$runtime_root" && pwd -P)
  config_root="$runtime_root/etc"
  enabled_ini="$config_root/conf.d/20-xdebug.ini"
  disabled_ini="$config_root/conf.d/20-xdebug.ini.disabled"

  install -d "$config_root/conf.d"
  install -m 0644 "$script_root/config/php.ini" "$config_root/php.ini"
  install -m 0644 "$script_root/config/xdebug.ini" "$disabled_ini"

  cleanup() {
    if [[ -f "$enabled_ini" ]]; then
      mv "$enabled_ini" "$disabled_ini"
    fi
  }
  trap cleanup EXIT

  "$script_root/runtime-command.sh" "$runtime_root" php -r '
    $expected = getenv("PHPRC");
    if (php_ini_loaded_file() !== $expected) {
        fwrite(STDERR, "unexpected php.ini: ".(php_ini_loaded_file() ?: "none").PHP_EOL);
        exit(1);
    }
    if (extension_loaded("xdebug")) {
        fwrite(STDERR, "xdebug loaded while disabled".PHP_EOL);
        exit(1);
    }
  '

  mv "$disabled_ini" "$enabled_ini"

  "$script_root/runtime-command.sh" "$runtime_root" php -r '
    if (!extension_loaded("xdebug")) {
        fwrite(STDERR, "xdebug did not load from conf.d".PHP_EOL);
        exit(1);
    }
  '

  fpm_info=$(
    "$script_root/runtime-command.sh" "$runtime_root" php-fpm -i
  )
  if [[ "$fpm_info" != *"Loaded Configuration File => $config_root/php.ini"* ]]; then
    printf 'FPM did not load the version-local php.ini for %s\n' "$runtime_root" >&2
    exit 1
  fi
  if [[ "$fpm_info" != *"Additional .ini files parsed => $enabled_ini"* ||
        "$fpm_info" != *"with Xdebug v"* ]]; then
    printf 'FPM did not load Xdebug from the version-local conf.d for %s\n' "$runtime_root" >&2
    exit 1
  fi

  mv "$enabled_ini" "$disabled_ini"
  trap - EXIT

  printf 'config passed php=%s root=%s\n' \
    "$("$script_root/runtime-command.sh" "$runtime_root" php -r 'echo PHP_VERSION;')" \
    "$runtime_root"
done
