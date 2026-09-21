# Agent Bridge Protocol Reference

This package exposes a small JSON-RPC-like API from Sublime Text to Pi over a Unix domain socket.

## Design invariants

- The Sublime package is the only server implementation.
- The Pi extension must route all bridge calls through `pi-extension/sublime-bridge.ts:callBridge()`.
- Do not shell out to `scripts/rpc.py` from the extension. `scripts/rpc.py` is a manual diagnostic client only.
- Do not add arbitrary Python eval/exec endpoints.
- Add explicit `rpc_*` functions and matching entries in `RPC_METHODS` for new capabilities.

## Discovery files

Primary discovery file:

```text
~/Library/Caches/Sublime Text/Cache/Agent Bridge/connection.json
```

Legacy discovery file, read for compatibility only:

```text
~/Library/Caches/Sublime Text/Cache/Sublime Agent Bridge/connection.json
```

Current `connection.json` fields:

```json
{
  "socketPath": "/Users/.../Library/Caches/Sublime Text/Cache/Agent Bridge/bridge.sock",
  "token": "random-url-safe-token",
  "pid": 12345,
  "package": "Agent Bridge",
  "name": "Sublime Agent Bridge",
  "protocolVersion": 1,
  "sublimeVersion": "4200"
}
```

Required by the Pi extension:

- `socketPath`
- `token`

## Transport and framing

Transport is Unix domain socket only.

Each request is one UTF-8 JSON object followed by `\n`.
Each response is one UTF-8 JSON object followed by `\n`.

Request shape:

```json
{
  "id": "request-id",
  "method": "status",
  "params": {},
  "token": "same-token-from-connection-json"
}
```

Success response shape:

```json
{
  "id": "request-id",
  "ok": true,
  "result": {}
}
```

Error response shape:

```json
{
  "ok": false,
  "error": "message",
  "traceback": "python traceback when available"
}
```

Unauthorized requests fail before method dispatch.

## Idle timeout lifecycle

Config setting:

```json
"idle_timeout_seconds": 3600
```

Persisted state file:

```text
~/Library/Caches/Sublime Text/Cache/Agent Bridge/bridge-state.json
```

State shape:

```json
{
  "activeUntil": 1779131015.6921961,
  "pid": 12345
}
```

Lifecycle rules:

- Manual start creates or refreshes `activeUntil`.
- Each authorized RPC request refreshes `activeUntil`.
- When the deadline expires while Sublime is running, the server stops and clears state.
- Plugin unload preserves state, so restart continuity is possible.
- On plugin load, the server auto-starts only when persisted `activeUntil` is still in the future.
- Manual stop clears state.

## RPC methods

Methods are registered in `sublime_agent_bridge.py:RPC_METHODS`.

### `ping`

Params: `{}`

Returns bridge identity and Sublime version.

### `status`

Params: `{}`

Returns server status, discovery paths, socket path, idle timeout/deadline, console capture status, and available method names.

### `list_windows`

Params: `{}`

Returns open Sublime windows, folders, active view ids, and active view files.

Touches Sublime UI state; implementation runs collection on the main thread.

### `list_views`

Params:

```json
{
  "window": "active"
}
```

`window` may be omitted, `"active"`, a window id, or a window index.

Returns view summaries for the selected window.

Touches Sublime UI state; implementation runs collection on the main thread.

### `scope_debug`

Params may include:

```json
{
  "window": "active",
  "view": 123,
  "point": 0,
  "line": 1,
  "row": 1,
  "row_base": 1,
  "col": 0,
  "column": 0,
  "context": 8
}
```

Returns cursor scope and nearby line/scope information.

Touches Sublime UI state; implementation runs collection on the main thread.

### `run_window_command`

Params:

```json
{
  "window": "active",
  "command": "command_name",
  "args": {}
}
```

Runs a Sublime window command if allowed by settings.

Touches Sublime UI state; implementation runs command on the main thread.

### `run_text_command`

Params:

```json
{
  "window": "active",
  "view": 123,
  "command": "command_name",
  "args": {}
}
```

Runs a Sublime text command against a matching view if allowed by settings.

Touches Sublime UI state; implementation runs command on the main thread.

### `list_output_panels`

Params:

```json
{
  "window": "active"
}
```

Returns output panel names, panel keys, view ids, and sizes.

Touches Sublime UI state; implementation runs collection on the main thread.

### `get_output_panel`

Params:

```json
{
  "window": "active",
  "panel": "diagnostics"
}
```

Returns output panel text, subject to `max_text_bytes` truncation.

Touches Sublime UI state; implementation runs collection on the main thread.

### `get_console_log`

Params may include:

```json
{
  "afterSequence": 123
}
```

Returns console messages captured while the bridge is running. Capture is implemented by temporarily wrapping Sublime's internal `sublime_api.log_message` when the bridge starts and restoring it when the bridge stops. This only captures future messages after bridge start; it cannot read pre-existing Ctrl-` console history.

The response includes `entries`, concatenated `text` subject to `max_text_bytes` truncation, `nextSequence`, and `hooked`.
