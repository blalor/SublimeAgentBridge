# Sublime Agent Bridge

Local bridge between a running Sublime Text instance and Pi. The default transport is an authenticated Unix domain socket; TCP localhost is available as a fallback.

This repository contains both halves:

- `sublime-package/Sublime Agent Bridge/` - Sublime Text package that runs an authenticated localhost JSON-RPC server in-process.
- `pi-extension/sublime-bridge.ts` - Pi extension that discovers the running bridge and exposes Sublime RPC methods as agent tools.

The bridge intentionally starts with a narrow, inspectable API instead of arbitrary Python eval.

## Install for local development

```bash
./scripts/install-dev.sh
```

This creates symlinks:

- Sublime package -> `~/Library/Application Support/Sublime Text/Packages/Sublime Agent Bridge`
- Pi extension -> `~/.pi/agent/extensions/sublime-bridge.ts`

Then restart Sublime or run `Preferences: Browse Packages` and confirm the package is loaded. In Pi, run `/reload` if already running.

## Sublime commands

Command Palette:

- `Sublime Agent Bridge: Start Server`
- `Sublime Agent Bridge: Stop Server`
- `Sublime Agent Bridge: Show Status`

The server writes discovery info, including the Unix socket path and bearer token, to:

```text
~/Library/Caches/Sublime Text/Cache/Sublime Agent Bridge/connection.json
```

## Pi tools

The Pi extension registers:

- `sublime_ping`
- `sublime_status`
- `sublime_list_windows`
- `sublime_list_views`
- `sublime_run_command`
- `sublime_get_output_panel`
- `sublime_env_doctor`

## Security

- Defaults to a Unix domain socket under Sublime's cache directory.
- TCP mode, if enabled, binds to `127.0.0.1` only.
- Requires a random bearer token in both transports.
- Discovery file and socket are written with mode `0600` where supported.
- No arbitrary Python eval endpoint.
