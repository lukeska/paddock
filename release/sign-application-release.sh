#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: release/sign-application-release.sh [--publish] TAG FINGERPRINT [OUTPUT_DIR]

Download an existing Paddock application release, verify its published
checksums, and create detached OpenPGP signatures for the exact source and
Arch package artifacts. With --publish, upload the verified signatures to the
same GitHub release.

The signing key must already be available through gpg. Private key material is
never read by this script directly and must not be stored in this repository.
EOF
}

publish=false
if [[ ${1:-} == --publish ]]; then
  publish=true
  shift
fi

if [[ $# -lt 2 || $# -gt 3 ]]; then
  usage >&2
  exit 2
fi

tag=$1
fingerprint=${2//[[:space:]]/}
output_dir=${3:-}

if [[ ! $tag =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
  echo "error: TAG must be an application release such as v0.1.2" >&2
  exit 2
fi
version=${BASH_REMATCH[1]}

fingerprint=${fingerprint^^}
if [[ ! $fingerprint =~ ^[0-9A-F]{40}$ ]]; then
  echo "error: FINGERPRINT must be a full 40-character OpenPGP fingerprint" >&2
  exit 2
fi

for command_name in gh gpg sha256sum; do
  if ! command -v "$command_name" >/dev/null; then
    echo "error: required command not found: $command_name" >&2
    exit 1
  fi
done

repo=${PADDOCK_GITHUB_REPOSITORY:-lukeska/paddock}
if [[ -z $output_dir ]]; then
  output_dir="release/signing/$tag"
fi
mkdir -p "$output_dir"
output_dir=$(cd "$output_dir" && pwd -P)

asset_names=$(gh release view "$tag" --repo "$repo" --json assets --jq '.assets[].name')
if grep -Eq '\.sig$' <<<"$asset_names"; then
  echo "error: $tag already contains signature assets; refusing to replace them" >&2
  exit 1
fi

source_name="paddock-$version.tar.gz"
mapfile -t package_names < <(grep -E "^paddock-$version-[0-9]+-x86_64\.pkg\.tar\.zst$" <<<"$asset_names")
if [[ ${#package_names[@]} -ne 1 ]]; then
  echo "error: expected exactly one x86_64 package for $tag, found ${#package_names[@]}" >&2
  exit 1
fi
package_name=${package_names[0]}

for required_asset in SHA256SUMS PKGBUILD "$source_name" "$package_name"; do
  if ! grep -Fxq "$required_asset" <<<"$asset_names"; then
    echo "error: release $tag is missing $required_asset" >&2
    exit 1
  fi
  if [[ -e $output_dir/$required_asset ]]; then
    echo "error: output already exists: $output_dir/$required_asset" >&2
    exit 1
  fi
done

gh release download "$tag" --repo "$repo" --dir "$output_dir" \
  --pattern SHA256SUMS --pattern PKGBUILD \
  --pattern "$source_name" --pattern "$package_name"

(
  cd "$output_dir"
  sha256sum --check SHA256SUMS
)

if ! gpg --batch --list-secret-keys "$fingerprint" >/dev/null 2>&1; then
  echo "error: no secret signing key is available for $fingerprint" >&2
  exit 1
fi

for artifact in "$source_name" "$package_name"; do
  signature="$output_dir/$artifact.sig"
  if [[ -e $signature ]]; then
    echo "error: signature already exists: $signature" >&2
    exit 1
  fi
  gpg --local-user "$fingerprint" --detach-sign \
    --output "$signature" "$output_dir/$artifact"
  gpg --status-fd 1 --verify "$signature" "$output_dir/$artifact" \
    | grep -q '^\[GNUPG:\] VALIDSIG '
done

echo "Signed and verified with $fingerprint:"
printf '  %s\n' "$output_dir/$source_name.sig" "$output_dir/$package_name.sig"

if [[ $publish == true ]]; then
  gh release upload "$tag" --repo "$repo" \
    "$output_dir/$source_name.sig" "$output_dir/$package_name.sig"
  echo "Published signatures to $repo release $tag"
else
  echo "Signatures were not uploaded. Re-run with --publish after reviewing them."
fi
