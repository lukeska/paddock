#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  printf 'usage: %s PACKAGE\n' "$0" >&2
  exit 2
fi

package=$1
case "$package" in
  *paddock-debug-*)
    printf 'refusing debug package: %s\n' "$package" >&2
    exit 1
    ;;
esac

test -f "$package"
pacman -U --needed --noconfirm "$package"
pacman -Q paddock
pacman -Qkk paddock

required=(
  /usr/bin/paddock
  /usr/bin/paddock-ui
  /usr/bin/paddock-tui
  /usr/lib/paddock/system-helper
  /usr/lib/paddock/php-fpm-launcher
  /usr/lib/paddock/check-ports
  /usr/lib/paddock/wait-for-socket
  /usr/lib/paddock/shims/php
  /usr/lib/paddock/shims/composer
  /usr/lib/paddock/shims/node
  /usr/lib/paddock/shims/npm
  /usr/lib/paddock/shims/npx
  /usr/share/paddock/artifacts.json
  /usr/share/paddock/composer.json
  /usr/share/paddock/node-artifacts.json
  /usr/share/applications/dev.paddock.Paddock.desktop
  /usr/share/metainfo/dev.paddock.Paddock.metainfo.xml
  /usr/share/doc/paddock/project-file.md
)
for path in "${required[@]}"; do
  test -e "$path" || {
    printf 'package is missing %s\n' "$path" >&2
    exit 1
  }
done

# Run as a normal desktop user, from outside the checkout, with clean XDG
# directories. This ensures Python resolves the installed module rather than
# accidentally importing the repository's source tree.
useradd --create-home paddock-ci
install -d -o paddock-ci -g paddock-ci \
  /tmp/paddock-ci/config /tmp/paddock-ci/data /tmp/paddock-ci/state \
  /tmp/paddock-ci/cache /tmp/paddock-ci/run /tmp/paddock-ci/work

runuser --user paddock-ci -- env \
  HOME=/home/paddock-ci \
  XDG_CONFIG_HOME=/tmp/paddock-ci/config \
  XDG_DATA_HOME=/tmp/paddock-ci/data \
  XDG_STATE_HOME=/tmp/paddock-ci/state \
  XDG_CACHE_HOME=/tmp/paddock-ci/cache \
  XDG_RUNTIME_DIR=/tmp/paddock-ci/run \
  PYTHONDONTWRITEBYTECODE=1 \
  bash -c '
    set -euo pipefail
    cd /tmp/paddock-ci/work
    paddock --version
    paddock help >/dev/null
    python -c '\''import pathlib, paddock; path = pathlib.Path(paddock.__file__).resolve(); assert str(path).startswith("/usr/lib/python"), path'\''
  '

# Package installation must never populate Paddock or Omarchy user config;
# setup is the explicit boundary for generated machine integration. The home
# may contain the distribution's normal /etc/skel files created by useradd.
test ! -e /home/paddock-ci/.config/paddock
test ! -e /home/paddock-ci/.local/share/paddock
test ! -e /home/paddock-ci/.config/omarchy
test ! -e /etc/systemd/system/paddock.target

bash -n \
  /usr/bin/paddock /usr/bin/paddock-ui \
  /usr/lib/paddock/php-fpm-launcher \
  /usr/lib/paddock/check-ports /usr/lib/paddock/wait-for-socket \
  /usr/lib/paddock/shims/php /usr/lib/paddock/shims/composer \
  /usr/lib/paddock/shims/node /usr/lib/paddock/shims/npm \
  /usr/lib/paddock/shims/npx

printf 'clean package install passed: %s\n' "$package"
