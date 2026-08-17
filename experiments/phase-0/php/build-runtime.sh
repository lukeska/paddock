#!/usr/bin/env bash

set -euo pipefail

script_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
php_version=${1:-}
spc_binary=${SPC_BIN:-}
workspace=${BUILD_WORKSPACE:-}

if [[ -z "$php_version" || -z "$spc_binary" || -z "$workspace" ]]; then
  printf 'Usage: SPC_BIN=/path/to/spc BUILD_WORKSPACE=/path/to/work %s <php-version>\n' "$0" >&2
  exit 2
fi

if [[ ! -x "$spc_binary" ]]; then
  printf 'StaticPHP builder is not executable: %s\n' "$spc_binary" >&2
  exit 1
fi

mkdir -p "$workspace"
craft_file="$workspace/craft-$php_version.yml"

sed "s/@PHP_VERSION@/$php_version/g" "$script_root/craft.yml.in" >"$craft_file"

printf 'Building PHP %s for glibc 2.17+ in %s\n' "$php_version" "$workspace"

(
  cd "$workspace"
  SPC_TARGET=native-native-gnu.2.17 "$spc_binary" craft "$craft_file" --no-interaction
)
