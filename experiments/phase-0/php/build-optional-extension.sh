#!/usr/bin/env bash

set -euo pipefail

extension=${1:-}
spc_binary=${SPC_BIN:-}
workspace=${BUILD_WORKSPACE:-}

if [[ -z "$extension" || -z "$spc_binary" || -z "$workspace" ]]; then
  printf 'Usage: SPC_BIN=/path/to/spc BUILD_WORKSPACE=/path/to/work %s <extension>\n' "$0" >&2
  exit 2
fi

runtime_php="$workspace/buildroot/bin/php"
craft_file=$(find "$workspace" -maxdepth 1 -name 'craft-*.yml' -print -quit)

if [[ ! -x "$runtime_php" || -z "$craft_file" ]]; then
  printf 'Build the base runtime before optional extensions: %s\n' "$workspace" >&2
  exit 1
fi

php_minor=$(
  "$runtime_php" -n -r 'echo PHP_MAJOR_VERSION, ".", PHP_MINOR_VERSION;'
)
static_extensions=$(
  awk '
    /^extensions:/ { in_extensions = 1; next }
    in_extensions && /^  - / { values = values separator substr($0, 5); separator = ","; next }
    in_extensions { exit }
    END { print values }
  ' "$craft_file"
)

if [[ -z "$static_extensions" ]]; then
  printf 'No base extensions found in %s\n' "$craft_file" >&2
  exit 1
fi

printf 'Building optional extension %s for PHP %s in %s\n' \
  "$extension" "$php_minor" "$workspace"

download_options=()
if [[ "$extension" == xdebug && ( "$php_minor" == 8.0 || "$php_minor" == 8.1 ) ]]; then
  # Xdebug 3.5 requires PHP 8.2+. Keep PHP 8.0 on the final 3.2 patch,
  # which supports PHP 8.0 while retaining the same module interface.
  download_options+=(
    --custom-url
    "xdebug:https://github.com/xdebug/xdebug/archive/refs/tags/3.2.2.tar.gz"
  )
fi

(
  cd "$workspace"
  "$spc_binary" download "$extension" --with-php="$php_minor" \
    "${download_options[@]}" --no-interaction
  SPC_TARGET=native-native-gnu.2.17 "$spc_binary" build "$static_extensions" \
    --build-cli \
    --build-fpm \
    --build-shared="$extension" \
    --no-interaction
)

module="$workspace/buildroot/modules/$extension.so"
if [[ ! -f "$module" ]]; then
  printf 'Expected module was not produced: %s\n' "$module" >&2
  exit 1
fi

"$runtime_php" -n \
  -d "extension_dir=$workspace/buildroot/modules" \
  -d "zend_extension=$extension" \
  --ri "$extension"
