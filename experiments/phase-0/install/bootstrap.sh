#!/usr/bin/env bash

set -euo pipefail

package_path=""
plugin_url=""
assume_yes=false

while (( $# > 0 )); do
  case "$1" in
    --package) package_path=${2:-}; shift 2 ;;
    --plugin-url) plugin_url=${2:-}; shift 2 ;;
    --yes) assume_yes=true; shift ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -eq 0 ]]; then
  printf 'Run as your desktop user, not root; sudo is requested only for pacman.\n' >&2
  exit 2
fi
if [[ ! -f "$package_path" || -z "$plugin_url" ]]; then
  printf 'Usage: %s --package FILE --plugin-url GIT_URL [--yes]\n' "$0" >&2
  exit 2
fi

printf '%s\n' \
  'Paddock will install:' \
  "  core package: $package_path" \
  "  Omarchy plugin: $plugin_url" \
  'The package is owned by pacman; the plugin is owned by Omarchy.'

if [[ "$assume_yes" != true ]]; then
  read -r -p 'Continue? [y/N] ' answer
  [[ "$answer" == y || "$answer" == Y ]] || exit 1
fi

sudo pacman -U --noconfirm "$package_path"

plugin_id=dev.paddock.status
if [[ -d "$HOME/.config/omarchy/plugins/$plugin_id/.git" ]]; then
  omarchy plugin update "$plugin_id" --yes
  omarchy plugin enable "$plugin_id"
else
  omarchy plugin add "$plugin_url" --enable --yes
fi

paddock doctor
printf 'Paddock core and Omarchy plugin installed successfully.\n'

