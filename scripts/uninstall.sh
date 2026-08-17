#!/usr/bin/env bash
set -euo pipefail
paddock uninstall
omarchy plugin remove dev.paddock.status --yes || true
omarchy pkg drop paddock
