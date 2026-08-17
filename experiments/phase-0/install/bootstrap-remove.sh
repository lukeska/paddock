#!/usr/bin/env bash

set -euo pipefail

if [[ $EUID -eq 0 ]]; then
  printf 'Run as your desktop user, not root.\n' >&2
  exit 2
fi

plugin_id=dev.paddock.status
if [[ -d "$HOME/.config/omarchy/plugins/$plugin_id" ]]; then
  omarchy plugin remove "$plugin_id" --yes
fi

if pacman -Q paddock-phase0 >/dev/null 2>&1; then
  sudo pacman -R --noconfirm paddock-phase0
fi

printf 'Paddock Phase 0 package and optional plugin removed.\n'
