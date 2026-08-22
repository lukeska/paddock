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
[[ $EUID -ne 0 ]] || { printf 'Run as your desktop user, not root.\n' >&2; exit 2; }
[[ -f "$package_path" && -n "$plugin_url" ]] || {
  printf 'Usage: %s --package FILE --plugin-url GIT_URL [--yes]\n' "$0" >&2
  exit 2
}
printf 'Core package (pacman): %s\nOptional plugin (Omarchy): %s\n' "$package_path" "$plugin_url"
if [[ "$assume_yes" != true ]]; then
  read -r -p 'Continue? [y/N] ' answer
  [[ "$answer" == y || "$answer" == Y ]] || exit 1
fi
sudo pacman -U --needed "$package_path"

# ADR 0008: "Plugin failure does not invalidate or silently remove a working
# core package." Under `set -e` an unguarded failure here aborted the script
# before `paddock setup`, leaving the package installed but the system
# unconfigured — the opposite of what that rule asks for. The plugin is
# optional; the CLI is the product.
install_plugin() {
  if [[ -d "$HOME/.config/omarchy/plugins/dev.paddock.status/.git" ]]; then
    omarchy plugin update dev.paddock.status --yes &&
      omarchy plugin enable dev.paddock.status
  else
    omarchy plugin add "$plugin_url" --enable --yes
  fi
}

if install_plugin; then
  printf 'Optional Omarchy plugin installed.\n'
else
  printf 'warning: the optional Omarchy plugin could not be installed.\n' >&2
  printf 'warning: Paddock itself is unaffected. Retry with:\n' >&2
  printf 'warning:   omarchy plugin add %s --enable\n' "$plugin_url" >&2
fi

paddock setup
