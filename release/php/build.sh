#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository=$(cd -- "$script_dir/../.." && pwd)
versions_file="$script_dir/versions.json"
selection=${1:-all}
architecture=$(uname -m)
source_date_epoch=${SOURCE_DATE_EPOCH:-1786924800}
work_root=${PADDOCK_RELEASE_WORK:-"$repository/release/work"}
dist_root=${PADDOCK_RELEASE_DIST:-"$repository/release/dist"}
cache_root=${PADDOCK_RELEASE_CACHE:-"${XDG_CACHE_HOME:-$HOME/.cache}/paddock-release"}
builder_archive="$cache_root/spc-2.8.5-linux-x86_64.tar.gz"
builder_dir="$cache_root/spc-2.8.5"
builder="$builder_dir/spc"

[[ "$architecture" == x86_64 ]] || { printf 'Unsupported release architecture: %s\n' "$architecture" >&2; exit 2; }
for command in curl jq sha256sum tar gzip cmake gperf re2c; do
  command -v "$command" >/dev/null || { printf 'Missing build dependency: %s\n' "$command" >&2; exit 2; }
done

mkdir -p "$cache_root" "$work_root" "$dist_root"
expected_builder=$(jq -r '.builder.sha256' "$versions_file")
builder_url=$(jq -r '.builder.url' "$versions_file")
if [[ ! -f "$builder_archive" || $(sha256sum "$builder_archive" | awk '{print $1}') != "$expected_builder" ]]; then
  temporary="$builder_archive.part"
  rm -f -- "$temporary"
  curl --fail --location --proto '=https' --tlsv1.2 "$builder_url" --output "$temporary"
  [[ $(sha256sum "$temporary" | awk '{print $1}') == "$expected_builder" ]] || {
    printf 'StaticPHP builder checksum mismatch.\n' >&2
    rm -f -- "$temporary"
    exit 1
  }
  mv "$temporary" "$builder_archive"
fi
if [[ ! -x "$builder" ]]; then
  rm -rf -- "$builder_dir"
  mkdir -p "$builder_dir"
  tar -xzf "$builder_archive" -C "$builder_dir"
  chmod 0755 "$builder"
fi

mapfile -t versions < <(jq -r --arg selection "$selection" '
  .runtimes[] | select($selection == "all" or .minor == $selection or .php == $selection) | .php
' "$versions_file")
(( ${#versions[@]} > 0 )) || { printf 'No configured runtime matches %s\n' "$selection" >&2; exit 2; }

runtime_roots=()
for php_version in "${versions[@]}"; do
  workspace="$work_root/php-$php_version-$architecture"
  log="$dist_root/paddock-php-$php_version-linux-$architecture.build.log"
  rm -rf -- "$workspace"
  mkdir -p "$workspace"
  printf 'Building PHP %s for %s\n' "$php_version" "$architecture" | tee "$log"
  (
    export SOURCE_DATE_EPOCH="$source_date_epoch"
    export SPC_BIN="$builder"
    export BUILD_WORKSPACE="$workspace"
    "$repository/experiments/phase-0/php/build-runtime.sh" "$php_version"
    "$repository/experiments/phase-0/php/build-optional-extension.sh" xdebug
    "$repository/experiments/phase-0/php/probe-runtime-config.sh" "$workspace/buildroot"
  ) 2>&1 | tee -a "$log"
  runtime_roots+=("$workspace/buildroot")
done

probe_environment=("REQUIRED_EXTENSIONS=curl dom fileinfo filter intl mbstring openssl pdo session tokenizer xml zip")
if [[ -n ${COMPOSER_PATH:-} ]]; then
  probe_environment+=("COMPOSER_PATH=$COMPOSER_PATH")
fi
env "${probe_environment[@]}" "$repository/experiments/phase-0/php/probe-runtimes.sh" "${runtime_roots[@]}"

for index in "${!versions[@]}"; do
  php_version=${versions[$index]}
  runtime=${runtime_roots[$index]}
  artifact="$dist_root/paddock-php-$php_version-linux-$architecture.tar.gz"
  assembly="$work_root/assembly-$php_version-$architecture"
  rm -rf -- "$assembly"
  mkdir -p "$assembly/runtime/bin" "$assembly/runtime/modules"
  install -m 0755 "$runtime/bin/php" "$assembly/runtime/bin/php"
  install -m 0755 "$runtime/bin/php-fpm" "$assembly/runtime/bin/php-fpm"
  cp -a -- "$runtime/modules/." "$assembly/runtime/modules/"
  for payload in etc license source-licenses build-extensions.json build-libraries.json; do
    if [[ -e "$runtime/$payload" ]]; then
      cp -a -- "$runtime/$payload" "$assembly/runtime/$payload"
    fi
  done
  find "$assembly/runtime" -exec touch --date="@$source_date_epoch" {} +
  tar --sort=name --format=posix --mtime="@$source_date_epoch" \
    --owner=0 --group=0 --numeric-owner -C "$assembly" -cf - runtime \
    | gzip -n -9 >"$artifact"
  python "$script_dir/metadata.py" \
    --artifact "$artifact" \
    --runtime "$assembly/runtime" \
    --php "$php_version" \
    --architecture "$architecture" \
    --builder-sha256 "$expected_builder" \
    --source-date-epoch "$source_date_epoch"
done

python "$script_dir/index.py" --dist "$dist_root" --output "$dist_root/artifacts.local.json"
printf 'Runtime release build completed: %s\n' "$dist_root"
