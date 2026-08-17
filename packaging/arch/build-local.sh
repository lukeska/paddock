#!/usr/bin/env bash
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository=$(cd -- "$script_dir/../.." && pwd)
version=$(awk -F'"' '/^version = / { print $2; exit }' "$repository/pyproject.toml")
release_version=${version%%.*}.${version#*.}
release_version=${release_version%.dev*}
archive="$script_dir/paddock-$release_version.tar.gz"
staging=$(mktemp -d)
trap 'rm -rf -- "$staging"' EXIT
mkdir -p "$staging/paddock-$release_version"
tar --exclude=.git --exclude=plans --exclude='*/__pycache__' --exclude='*.py[cod]' \
  --exclude='*.pkg.tar.*' --exclude='packaging/arch/paddock-*.tar.gz' \
  --exclude=packaging/arch/src --exclude=packaging/arch/pkg \
  --exclude=release/work --exclude=release/dist --exclude=log \
  -C "$repository" -cf - . | tar -C "$staging/paddock-$release_version" -xf -
tar -C "$staging" -czf "$archive" "paddock-$release_version"
sha256=$(sha256sum "$archive" | awk '{print $1}')
sed -i "s/^sha256sums=.*/sha256sums=('$sha256')/" "$script_dir/PKGBUILD"
(
  cd "$script_dir"
  makepkg --cleanbuild --clean --force
)
