#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

sublime_packages="$HOME/Library/Application Support/Sublime Text/Packages"
pi_extensions="$HOME/.pi/agent/extensions"
sublime_package_dir="$sublime_packages/Sublime Agent Bridge"

mkdir -p "$sublime_packages" "$pi_extensions"

# Sublime loads regular package files most reliably. Copy this package into
# Packages/ for runtime, and re-run this installer after edits.
if [[ -L "$sublime_package_dir" ]]; then
  rm "$sublime_package_dir"
fi
mkdir -p "$sublime_package_dir"
rm -f \
  "$sublime_package_dir/sublime_agent_bridge.py" \
  "$sublime_package_dir/Default.sublime-commands" \
  "$sublime_package_dir/Sublime Agent Bridge.sublime-settings"
cp \
  "$repo_root/sublime-package/Sublime Agent Bridge/sublime_agent_bridge.py" \
  "$repo_root/sublime-package/Sublime Agent Bridge/Default.sublime-commands" \
  "$repo_root/sublime-package/Sublime Agent Bridge/Sublime Agent Bridge.sublime-settings" \
  "$repo_root/sublime-package/Sublime Agent Bridge/.python-version" \
  "$sublime_package_dir/"

# Keep the Pi extension live-linked to the repo so /reload picks up edits.
ln -sfn "$repo_root/pi-extension/sublime-bridge.ts" "$pi_extensions/sublime-bridge.ts"

printf 'Installed Sublime package files in:\n  %s\n' "$sublime_package_dir"
printf 'Linked Pi extension:\n  %s -> %s\n' \
  "$pi_extensions/sublime-bridge.ts" \
  "$repo_root/pi-extension/sublime-bridge.ts"
