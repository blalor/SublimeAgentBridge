# Sublime Agent Bridge

Local bridge between a running Sublime Text instance and [Pi](https://pi.dev) over an authenticated Unix domain socket.

This repository contains both halves:

- this directory - Sublime Text package that runs an authenticated Unix-socket JSON-RPC server in-process.
- `pi-extension/sublime-bridge.ts` - Pi extension that discovers the running bridge and exposes Sublime RPC methods as agent tools.

The bridge intentionally starts with a narrow, inspectable API instead of arbitrary Python eval.

## Install for local development

1. Clone this repository at `~/Library/Application Support/Sublime Text/Packages/Agent Bridge`.
2. Install the bundled Pi package:

   ```bash
   pi install "$HOME/Library/Application Support/Sublime Text/Packages/Agent Bridge"
   ```

Then restart Sublime or run `Preferences: Browse Packages` and confirm the package is loaded.  
In Pi, run `/reload` if already running.

## Sublime commands

Command Palette:

- `Sublime Agent Bridge: Start Server`
- `Sublime Agent Bridge: Stop Server`
- `Sublime Agent Bridge: Show Status`

The server writes discovery info, including the Unix socket path and bearer token, to:

```text
~/Library/Caches/Sublime Text/Cache/Agent Bridge/connection.json
```

The bridge also persists its idle deadline to:

```text
~/Library/Caches/Sublime Text/Cache/Agent Bridge/bridge-state.json
```

The server stops automatically after `idle_timeout_seconds` without an authorized RPC request. If Sublime restarts while that persisted deadline is still in the future, the package auto-starts the bridge again; otherwise it stays stopped.

`scripts/rpc.py` is an optional manual diagnostic client for calling the same JSON-RPC methods from a shell. The Pi extension must never shell out to it or use it for bridge access. All Pi tools and commands must make RPC calls only through the extension's internal `callBridge()` JSON-RPC client.

## Pi resources

The Pi extension contributes a `sublime-text-api` skill via `resources_discover`. Pi only advertises the skill description in the system prompt; the full skill and reference files are loaded on demand. The skill points to the official Sublime Text API reference and includes a local bridge protocol reference:

```text
https://www.sublimetext.com/docs/api_reference.html
pi-extension/skills/sublime-text-api/references/bridge-protocol.md
```

## Pi tools

The Pi extension registers the bridge tools but keeps them inactive by default so they do not occupy model tool context for unrelated tasks. They are activated when the `sublime-text-api` skill is invoked with `/skill:sublime-text-api` or when the agent loads that skill file on demand:

- `sublime_ping`
- `sublime_status`
- `sublime_list_windows`
- `sublime_list_views`
- `sublime_scope_debug`
- `sublime_run_command`
- `sublime_list_output_panels`
- `sublime_get_output_panel`

## Security

- Uses a Unix domain socket under Sublime's cache directory by default.
- Requires a random bearer token.
- Stops automatically after the configured idle timeout.
- Discovery/state files and socket are written with mode `0600` where supported.
- No arbitrary Python eval endpoint.
