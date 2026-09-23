#!/usr/bin/env bash
set -euo pipefail

# Served as install.sh on each application release. Keep this independent of
# Paddock: it must work before the package has been installed.
if (( EUID == 0 )); then
  printf 'Run this installer as your desktop user, not root.\n' >&2
  exit 2
fi

for command_name in curl sudo pacman sha256sum uname mktemp awk; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'Required command is missing: %s\n' "$command_name" >&2
    exit 1
  fi
done

architecture=$(uname -m)
if [[ $architecture != x86_64 ]]; then
  printf 'No published Paddock package is available for %s.\n' "$architecture" >&2
  exit 1
fi

release_url=https://github.com/lukeska/paddock/releases/latest/download
work_dir=$(mktemp -d)
trap 'rm -rf -- "$work_dir"' EXIT

printf 'Fetching the latest Paddock release manifest...\n'
curl --fail --silent --show-error --location --proto '=https' \
  --proto-redir '=https' --retry 3 \
  --output "$work_dir/SHA256SUMS" "$release_url/SHA256SUMS"

awk -v arch="$architecture" '
  $2 ~ ("^paddock-[0-9]+\\.[0-9]+\\.[0-9]+-[0-9]+-" arch "\\.pkg\\.tar\\.zst$") { print }
' "$work_dir/SHA256SUMS" > "$work_dir/package.sha256"

if [[ $(wc -l < "$work_dir/package.sha256") != 1 ]]; then
  printf 'The release manifest must name exactly one %s package.\n' "$architecture" >&2
  exit 1
fi

read -r package_sha package_name < "$work_dir/package.sha256"
if [[ ! $package_sha =~ ^[[:xdigit:]]{64}$ ]]; then
  printf 'The release manifest contains an invalid package checksum.\n' >&2
  exit 1
fi

printf 'Downloading %s...\n' "$package_name"
curl --fail --silent --show-error --location --proto '=https' \
  --proto-redir '=https' --retry 3 \
  --output "$work_dir/$package_name" "$release_url/$package_name"

(cd "$work_dir" && sha256sum --check package.sha256)

sudo pacman -U --needed --noconfirm -- "$work_dir/$package_name" </dev/null
paddock setup --yes </dev/null
paddock doctor </dev/null
