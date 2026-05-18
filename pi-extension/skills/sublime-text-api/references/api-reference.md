# Sublime Text API Reference

Official documentation:

- https://www.sublimetext.com/docs/api_reference.html

Use that page for current signatures and behavior for the `sublime` and `sublime_plugin` APIs, including:

- `sublime.Window`
- `sublime.View`
- `sublime.Region`
- selections and scopes
- output panels
- commands (`ApplicationCommand`, `WindowCommand`, `TextCommand`)
- settings and package lifecycle hooks

Bridge-specific notes:

- Sublime Text UI/API interactions should happen on the main thread. In this package, wrap such work with `run_on_main_thread()`.
- Bridge methods should be explicit JSON-RPC methods in `RPC_METHODS`; avoid generic Python eval/exec functionality.
- The Pi extension must call the bridge only through its internal `callBridge()` helper. Do not shell out to `scripts/rpc.py` or construct ad-hoc RPC clients in the extension.
- For package-specific discovery, framing, idle timeout, and RPC method details, see [bridge-protocol.md](bridge-protocol.md).
