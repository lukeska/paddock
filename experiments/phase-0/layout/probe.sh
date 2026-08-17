#!/usr/bin/env bash

set -euo pipefail

script_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
temporary_dir=$(mktemp -d)
trap 'rm -rf -- "$temporary_dir"' EXIT

fake_home="$temporary_dir/Home üser with spaces"
mkdir -p "$fake_home" "$temporary_dir/runtime one"

export HOME="$fake_home"
export XDG_CONFIG_HOME="$temporary_dir/Config ü"
export XDG_DATA_HOME="$temporary_dir/Data with spaces"
export XDG_STATE_HOME="$temporary_dir/State ü"
export XDG_CACHE_HOME="$temporary_dir/Cache with spaces"
export XDG_RUNTIME_DIR="$temporary_dir/runtime one"

"$script_root/layout.py" init
paths=$("$script_root/layout.py" paths)
for root in config data state runtime cache; do
  path=$(awk -F= -v name="$root" '$1 == name {sub(/^[^=]*=/, ""); print}' <<<"$paths")
  [[ -d "$path" && $(stat -c '%a' "$path") == 700 ]]
done

config_file="$XDG_CONFIG_HOME/paddock/sites.json"
"$script_root/layout.py" atomic-write "$config_file" generation-1
[[ $(cat "$config_file") == generation-1 && $(stat -c '%a' "$config_file") == 600 ]]
set +e
"$script_root/layout.py" atomic-write "$config_file" generation-2 --simulate-enospc \
  >"$temporary_dir/enospc.out" 2>"$temporary_dir/enospc.err"
enospc_status=$?
set -e
[[ $enospc_status != 0 && $(cat "$config_file") == generation-1 ]]
if find "$(dirname "$config_file")" -maxdepth 1 -name '.sites.json.*' | grep -q .; then
  printf 'Interrupted update left a temporary file.\n' >&2
  exit 1
fi
"$script_root/layout.py" atomic-write "$config_file" generation-2
[[ $(cat "$config_file") == generation-2 ]]

unset XDG_CONFIG_HOME XDG_DATA_HOME XDG_STATE_HOME XDG_CACHE_HOME
fallbacks=$("$script_root/layout.py" paths)
[[ "$fallbacks" == *"config=$fake_home/.config/paddock"* ]]
[[ "$fallbacks" == *"data=$fake_home/.local/share/paddock"* ]]
[[ "$fallbacks" == *"state=$fake_home/.local/state/paddock"* ]]
[[ "$fallbacks" == *"cache=$fake_home/.cache/paddock"* ]]

unset XDG_RUNTIME_DIR
set +e
runtime_error=$("$script_root/layout.py" paths 2>&1)
runtime_status=$?
set -e
[[ $runtime_status != 0 && "$runtime_error" == *'XDG_RUNTIME_DIR is required'* ]]

printf 'XDG layout passed unicode+spaces=yes modes=0700 files=0600 fallbacks=yes runtime-required=yes atomic=yes enospc=rollback\n'
