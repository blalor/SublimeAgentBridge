#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

sublime_packages="$HOME/Library/Application Support/Sublime Text/Packages"
pi_extensions="$HOME/.pi/agent/extensions"

mkdir -p "$sublime_packages" "$pi_extensions"

ln -sfn "$repo_root/sublime-package/Sublime Agent Bridge" "$sublime_packages/Sublime Agent Bridge"
ln -sfn "$repo_root/pi-extension/sublime-bridge.ts" "$pi_extensions/sublime-bridge.ts"

printf 'Linked Sublime package:\n  %s -> %s\n' \
  "$sublime_packages/Sublime Agent Bridge" \
  "$repo_root/sublime-package/Sublime Agent Bridge"
printf 'Linked Pi extension:\n  %s -> %s\n' \
  "$pi_extensions/sublime-bridge.ts" \
  "$repo_root/pi-extension/sublime-bridge.ts"
