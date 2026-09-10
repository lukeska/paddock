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

# PHP 8.0's libxml extension still consumes ATTRIBUTE_UNUSED from libxml2's
# public headers. libxml2 2.14 removed that compatibility macro, so keep this
# EOL runtime on the final 2.12 patch line rather than patching PHP source in a
# way that would be harder to audit. Newer PHP minors continue to use SPC's
# current libxml2 selection.
if [[ "$php_version" == 8.0.* || "$php_version" == 8.1.* ]]; then
  cat >>"$craft_file" <<'YAML'

download-options:
  custom-url:
    - "libxml2:https://download.gnome.org/sources/libxml2/2.12/libxml2-2.12.10.tar.xz"
    - "libxslt:https://download.gnome.org/sources/libxslt/1.1/libxslt-1.1.42.tar.xz"
    - "icu:https://github.com/unicode-org/icu/releases/download/release-72-1/icu4c-72_1-src.tgz"
YAML

  # Clang 16 and newer default to a C language mode that rejects the K&R-style
  # function definitions retained in PHP 8.0's bundled libbcmath. Keep the
  # builder's normal flags while selecting a pre-C23 GNU mode that accepts
  # those definitions and remains compatible with current dependencies.
  export SPC_DEFAULT_C_FLAGS="-fPIC -Os -std=gnu17"
fi

printf 'Building PHP %s for glibc 2.17+ in %s\n' "$php_version" "$workspace"

(
  cd "$workspace"
  SPC_TARGET=native-native-gnu.2.17 "$spc_binary" craft "$craft_file" --no-interaction
)
