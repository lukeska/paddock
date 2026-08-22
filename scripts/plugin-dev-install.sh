#!/usr/bin/env bash
# Copy the working-tree plugin into the Omarchy plugin directory.
#
# Development only. ADR 0008 makes `omarchy plugin add/update/enable/remove`
# the supported lifecycle and forbids pacman owning that directory; this script
# exists so a change can be loaded without publishing it anywhere first.
#
# It copies rather than symlinks on purpose: the plugin registry rejects a
# symlink anywhere inside a plugin folder. Saving into the destination triggers
# the registry's inotify watch, so edits hot-reload without a restart.
set -euo pipefail

id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' \
  "$(dirname -- "${BASH_SOURCE[0]}")/../plugin/manifest.json")
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../plugin" && pwd)
target="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/$id"

if [ "$(id -u)" = 0 ]; then
  echo "refusing to run as root: this installs into a user's own config" >&2
  exit 1
fi

mkdir -p "$target"
# Mirror exactly, so a file deleted from the tree also disappears here.
rm -rf -- "${target:?}"/*
cp -R -- "$source_dir"/. "$target"/
find "$target" -type l -delete          # the registry rejects any symlink
echo "installed $id -> $target"

if command -v omarchy-shell >/dev/null 2>&1; then
  omarchy-shell shell rescanPlugins >/dev/null 2>&1 \
    && echo "shell rescanned" \
    || echo "shell not running; it will pick this up at next start"
fi

cat <<NOTICE

Enable it once with:
  omarchy plugin enable $id --section right

Then check it:
  omarchy-shell shell listPlugins | grep $id
  omarchy-shell -q $id refresh
NOTICE
