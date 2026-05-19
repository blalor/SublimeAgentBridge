---
name: sublime-text-api
description: Use when writing or refactoring Sublime Text packages, commands, plugins, settings, output panels, views, windows, regions, selections, scopes, or bridge RPC methods that call the Sublime Text Python API.
---

# Sublime Text API

Use this skill when working on Sublime Text package/plugin code or when implementing bridge RPC methods that interact with Sublime Text.

## API Reference

The authoritative Sublime Text Python API reference is:

<https://www.sublimetext.com/docs/api_reference.html>

A short local reference with the URL and bridge-specific guidance is in [references/api-reference.md](references/api-reference.md).

For this package's bridge protocol, discovery files, idle timeout lifecycle, and RPC methods, see [references/bridge-protocol.md](references/bridge-protocol.md).

## Guidance

- Prefer the official API reference for exact method names, signatures, and object behavior.
- Remember that Sublime Text API calls that touch UI state generally need to run on the main thread; this package uses `run_on_main_thread()` for that.
- Keep bridge RPC methods narrow and explicit. Do not add arbitrary Python evaluation endpoints.
- If adding a new Pi tool, add a matching explicit `rpc_*` function in `sublime_agent_bridge.py` and route the extension tool through `callBridge()`.
- Loading this skill activates the Sublime bridge Pi tools for the session (`sublime_ping`, `sublime_status`, `sublime_list_windows`, `sublime_list_views`, `sublime_scope_debug`, `sublime_run_command`, `sublime_list_output_panels`, `sublime_get_output_panel`).
- For extension-to-plugin connectivity checks, use `sublime_ping` and `sublime_status`.
- Do not use `scripts/rpc.py` unless the user explicitly asks to test the standalone diagnostic script.
- The Pi extension must never shell out to `scripts/rpc.py` or construct ad-hoc RPC clients; all extension RPC calls must go through the internal `callBridge()` helper.
