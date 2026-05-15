# Sublime Agent Bridge

Local bridge between a running Sublime Text instance and Pi. The default transport is an authenticated Unix domain socket; TCP localhost is available as a fallback.

This repository contains both halves:

- `sublime-package/Sublime Agent Bridge/` - Sublime Text package that runs an authenticated local RPC server in-process.
- `pi-extension/sublime-bridge.ts` - Pi extension that discovers the running bridge and exposes Sublime RPC methods as agent tools.

The bridge intentionally starts with a narrow, inspectable API instead of arbitrary Python eval.

## Install for local development

```bash
./scripts/install-dev.sh
```

This installs/copies the Sublime package and symlinks the Pi extension:

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
- `sublime_resolve_environment`
- `sublime_which`
- `sublime_env_doctor`

## Smoke test

```bash
./scripts/rpc.sh ping
./scripts/rpc.sh list_windows
./scripts/rpc.sh which '{"window":"active","tools":["shellcheck","uv"]}'
```

## Deterministic environment resolution

The bridge can resolve a window-specific execution environment without changing Sublime UI state:

1. collect the window folder/active file in-process,
2. start from a clean allowlisted base environment rather than Sublime's inherited `PATH`,
3. discover `direnv` from configured bootstrap paths,
4. run `direnv export json` in the nearest `.envrc` directory,
5. use the returned `PATH`/vars for tool discovery or future process launches.

RPC methods:

- `resolve_environment`: returns resolved PATH, selected vars, and optional tool paths.
- `which`: returns command paths after applying the resolved direnv/Flox environment.

## Security

- Defaults to a Unix domain socket under Sublime's cache directory.
- TCP mode, if enabled, binds to `127.0.0.1` only.
- Requires a random bearer token in both transports.
- Discovery file and socket are written with mode `0600` where supported.
- No arbitrary Python eval endpoint.
