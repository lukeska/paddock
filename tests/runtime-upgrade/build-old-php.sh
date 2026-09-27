#!/usr/bin/env bash
set -euo pipefail

repository=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
versions=$(mktemp)
trap 'rm -f -- "$versions"' EXIT
jq '(.runtimes[] | select(.minor == "8.5") | .php) = "8.5.8"' \
  "$repository/release/php/versions.json" > "$versions"

export PADDOCK_RELEASE_VERSIONS_FILE="$versions"
export PADDOCK_RELEASE_WORK="${PADDOCK_RELEASE_WORK:-$repository/release/work-fixture}"
export PADDOCK_RELEASE_DIST="${PADDOCK_RELEASE_DIST:-$repository/release/dist-fixture}"
export PADDOCK_RELEASE_DOWNLOAD_CACHE="${PADDOCK_RELEASE_DOWNLOAD_CACHE:-$repository/release/work/download-cache}"
"$repository/release/php/build.sh" 8.5
printf 'Older test archive: %s/paddock-php-8.5.8-linux-x86_64.tar.gz\n' "$PADDOCK_RELEASE_DIST"
