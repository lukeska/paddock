#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  printf 'usage: %s OUTPUT VERSION\n' "$0" >&2
  exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository=$(cd -- "$script_dir/.." && pwd)
output=$1
version=$2

case "$version" in
  ''|*[!0-9.]*|.*|*.)
    printf 'invalid release version: %s\n' "$version" >&2
    exit 2
    ;;
esac

expected=$(awk -F'"' '/^version = / { print $2; exit }' "$repository/pyproject.toml")
if [ "$version" != "$expected" ]; then
  printf 'release version %s does not match project version %s\n' "$version" "$expected" >&2
  exit 1
fi

output_dir=$(dirname -- "$output")
mkdir -p "$output_dir"
output=$(cd -- "$output_dir" && pwd)/$(basename -- "$output")
file_list=$(mktemp)
archive=$(mktemp)
compressed="$output.tmp"
trap 'rm -f -- "$file_list" "$archive" "$compressed"' EXIT

# The release PKGBUILD is intentionally absent from its own source archive.
# That breaks the otherwise circular relationship between the recipe's pinned
# checksum and the archive containing that checksum. A clean release checkout
# contains tracked files only; including non-ignored working-tree files also
# makes the same builder useful while developing the workflow locally.
while IFS= read -r -d '' relative; do
  [ "$relative" = packaging/arch/PKGBUILD ] && continue
  if [ -e "$repository/$relative" ] || [ -L "$repository/$relative" ]; then
    printf '%s\0' "$relative"
  fi
done < <(git -C "$repository" ls-files --cached --others --exclude-standard -z | LC_ALL=C sort -z) \
  > "$file_list"

tar --null --no-recursion --format=gnu \
  --owner=0 --group=0 --numeric-owner --mtime=@0 \
  --transform="s,^,paddock-$version/," \
  -cf "$archive" -C "$repository" --files-from="$file_list"
gzip -n -9 < "$archive" > "$compressed"
mv -- "$compressed" "$output"
printf '%s\n' "$output"
