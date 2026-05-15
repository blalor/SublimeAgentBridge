#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

sublime_packages="$HOME/Library/Application Support/Sublime Text/Packages"
pi_extensions="$HOME/.pi/agent/extensions"
sublime_package_dir="$sublime_packages/Sublime Agent Bridge"

mkdir -p "$sublime_packages" "$pi_extensions"

# Sublime may not reliably discover a newly-created symlinked package directory
# in a running instance. Use a real package directory and symlink each source
# file back into this repository instead.
if [[ -L "$sublime_package_dir" ]]; then
  rm "$sublime_package_dir"
fi
mkdir -p "$sublime_package_dir"
for file in \
  "sublime_agent_bridge.py" \
  "Default.sublime-commands" \
  "Sublime Agent Bridge.sublime-settings"
do
  ln -sfn "$repo_root/sublime-package/Sublime Agent Bridge/$file" "$sublime_package_dir/$file"
done

ln -sfn "$repo_root/pi-extension/sublime-bridge.ts" "$pi_extensions/sublime-bridge.ts"

printf 'Linked Sublime package files in:\n  %s\n' "$sublime_package_dir"
printf 'Linked Pi extension:\n  %s -> %s\n' \
  "$pi_extensions/sublime-bridge.ts" \
  "$repo_root/pi-extension/sublime-bridge.ts"
