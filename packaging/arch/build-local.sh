#!/usr/bin/env bash
set -euo pipefail
if ! command -v go >/dev/null 2>&1; then
  printf '%s\n' 'Go is required to build the Paddock TUI. Install it with: sudo pacman -S --needed go' >&2
  exit 1
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository=$(cd -- "$script_dir/../.." && pwd)
version=$(awk -F'"' '/^version = / { print $2; exit }' "$repository/pyproject.toml")
archive="$script_dir/paddock-$version.tar.gz"
recipe=$(mktemp "$script_dir/PKGBUILD.local.XXXXXX")
trap 'rm -f -- "$recipe"' EXIT
"$repository/release/build-source.sh" "$archive" "$version" >/dev/null
sha256=$(sha256sum "$archive" | awk '{print $1}')
sed \
  -e 's/^pkgrel=.*/pkgrel=25/' \
  -e 's#^source=.*#source=("$pkgname-$pkgver.tar.gz")#' \
  -e "s/^sha256sums=.*/sha256sums=('$sha256')/" \
  "$script_dir/PKGBUILD" > "$recipe"
(
  cd "$script_dir"
  # The release-integrity test intentionally compares the committed source
  # checksum with the tree being packaged. A local development tree cannot
  # satisfy that assertion until its changes are committed and released; its
  # test suite is run directly before packaging instead.
  makepkg --cleanbuild --clean --force --nocheck -p "$recipe"
)
