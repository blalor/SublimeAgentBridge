import json
import os
import secrets
import socketserver
import threading
import time
import traceback

import sublime
import sublime_api
import sublime_plugin


PACKAGE = "Agent Bridge"
DISPLAY_NAME = "Sublime Agent Bridge"
SETTINGS = "Agent Bridge.sublime-settings"
BRIDGE_PROTOCOL_VERSION = 1
CONNECTION_FILE = os.path.join(sublime.cache_path(), PACKAGE, "connection.json")
LEGACY_CONNECTION_FILE = os.path.join(sublime.cache_path(), DISPLAY_NAME, "connection.json")
STATE_FILE = os.path.join(sublime.cache_path(), PACKAGE, "bridge-state.json")
LOG_FILE = os.path.join(sublime.cache_path(), PACKAGE, "bridge.log")
DEFAULT_SOCKET_PATH = os.path.join(sublime.cache_path(), PACKAGE, "bridge.sock")

_server = None
_server_lock = threading.RLock()
_idle_timer_generation = 0
_console_log_lock = threading.RLock()
_console_log_entries = []
_console_log_next_sequence = 1
_console_log_original_message = None
_console_log_hook = None


def log(message):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(message + "\n")
    except Exception:
        pass


def settings():
    return sublime.load_settings(SETTINGS)


def max_text_bytes():
    return int(settings().get("max_text_bytes", 512 * 1024))


def console_log_max_entries():
    return max(1, int(settings().get("console_log_max_entries", 2000)))


def idle_timeout_seconds():
    return max(1, int(settings().get("idle_timeout_seconds", 60 * 60)))


def read_active_until():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return float((json.load(f) or {}).get("activeUntil", 0))
    except (FileNotFoundError, ValueError, TypeError, OSError, json.JSONDecodeError):
        return 0


def write_bridge_state(active_until):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"activeUntil": active_until, "pid": os.getpid()}, f, indent=2, sort_keys=True)
    try:
        os.chmod(STATE_FILE, 0o600)
    except OSError:
        pass


def clear_bridge_state():
    try:
        os.unlink(STATE_FILE)
    except FileNotFoundError:
        pass
    except OSError:
        pass


# The idle deadline is persisted so plugin reloads/Sublime restarts only auto-start
# the bridge while a previously-started server would still have been active.
# Manual start and authorized RPC requests refresh it; manual stop and idle expiry clear it.
def refresh_idle_deadline():
    active_until = time.time() + idle_timeout_seconds()
    write_bridge_state(active_until)
    schedule_idle_check(active_until)
    return active_until


def schedule_idle_check(active_until):
    global _idle_timer_generation
    with _server_lock:
        _idle_timer_generation += 1
        generation = _idle_timer_generation
    delay_ms = max(1000, int((active_until - time.time()) * 1000) + 250)
    sublime.set_timeout(lambda: check_idle_timeout(generation), delay_ms)


def check_idle_timeout(generation):
    with _server_lock:
        if generation != _idle_timer_generation or _server is None:
            return
    active_until = read_active_until()
    now = time.time()
    if active_until > now:
        schedule_idle_check(active_until)
        return
    log("idle timeout expired")
    stop_server(clear_state=True)


def truncate_text(text):
    limit = max_text_bytes()
    data = text.encode("utf-8")
    if len(data) <= limit:
        return {"text": text, "truncated": False, "bytes": len(data)}
    truncated = data[:limit].decode("utf-8", "replace")
    return {"text": truncated, "truncated": True, "bytes": len(data), "returnedBytes": limit}


def normalize_console_message(message):
    if isinstance(message, bytes):
        return message.decode("utf-8", "replace")
    return str(message)


def append_console_log(message):
    global _console_log_next_sequence
    text = normalize_console_message(message)
    with _console_log_lock:
        entry = {
            "sequence": _console_log_next_sequence,
            "time": time.time(),
            "text": text,
        }
        _console_log_next_sequence += 1
        _console_log_entries.append(entry)
        overflow = len(_console_log_entries) - console_log_max_entries()
        if overflow > 0:
            del _console_log_entries[:overflow]


def install_console_log_hook():
    global _console_log_original_message, _console_log_hook
    with _console_log_lock:
        if _console_log_hook is not None and sublime_api.log_message is _console_log_hook:
            return True

        original = sublime_api.log_message

        def hooked_log_message(message):
            try:
                append_console_log(message)
            except Exception:
                pass
            return original(message)

        _console_log_original_message = original
        _console_log_hook = hooked_log_message
        sublime_api.log_message = hooked_log_message
        return True


def uninstall_console_log_hook():
    global _console_log_original_message, _console_log_hook
    with _console_log_lock:
        if _console_log_hook is None:
            return False
        if sublime_api.log_message is _console_log_hook:
            sublime_api.log_message = _console_log_original_message
            restored = True
        else:
            # Another plugin wrapped log_message after Agent Bridge. Avoid clobbering it.
            restored = False
        _console_log_original_message = None
        _console_log_hook = None
        return restored


def console_log_hooked():
    with _console_log_lock:
        return _console_log_hook is not None and sublime_api.log_message is _console_log_hook


def collect_console_log(params):
    after_sequence = params.get("afterSequence") or params.get("after") or 0
    try:
        after_sequence = int(after_sequence)
    except (TypeError, ValueError):
        after_sequence = 0
    with _console_log_lock:
        entries = [dict(entry) for entry in _console_log_entries if entry["sequence"] > after_sequence]
        next_sequence = _console_log_next_sequence
        hooked = console_log_hooked()
    text = "".join(entry["text"] for entry in entries)
    return dict({
        "hooked": hooked,
        "entries": entries,
        "entryCount": len(entries),
        "nextSequence": next_sequence,
    }, **truncate_text(text))


def rpc_get_console_log(params):
    return collect_console_log(params)


def write_connection_file(data):
    os.makedirs(os.path.dirname(CONNECTION_FILE), exist_ok=True)
    with open(CONNECTION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    try:
        os.chmod(CONNECTION_FILE, 0o600)
    except OSError:
        pass


def remove_connection_file():
    for path in (CONNECTION_FILE, LEGACY_CONNECTION_FILE):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def run_on_main_thread(fn, timeout=10.0):
    event = threading.Event()
    box = {}

    def runner():
        try:
            box["result"] = fn()
        except Exception:
            box["error"] = traceback.format_exc()
        finally:
            event.set()

    sublime.set_timeout(runner, 0)
    if not event.wait(timeout):
        raise TimeoutError("Timed out waiting for Sublime main thread")
    if "error" in box:
        raise RuntimeError(box["error"])
    return box.get("result")


def rpc_ping(_params):
    return {
        "ok": True,
        "package": PACKAGE,
        "name": DISPLAY_NAME,
        "protocolVersion": BRIDGE_PROTOCOL_VERSION,
        "sublimeVersion": sublime.version(),
    }


def rpc_status(_params):
    server = get_server()
    active_until = read_active_until()
    return {
        "running": server is not None,
        "package": PACKAGE,
        "name": DISPLAY_NAME,
        "protocolVersion": BRIDGE_PROTOCOL_VERSION,
        "connectionFile": CONNECTION_FILE,
        "legacyConnectionFile": LEGACY_CONNECTION_FILE,
        "socketPath": server.socket_path if server else None,
        "pid": os.getpid(),
        "idleTimeoutSeconds": idle_timeout_seconds(),
        "activeUntil": active_until,
        "activeRemainingSeconds": max(0, int(active_until - time.time())),
        "consoleLogHooked": console_log_hooked(),
        "consoleLogEntries": len(_console_log_entries),
        "methods": sorted(RPC_METHODS.keys()),
    }


def rpc_list_windows(_params):
    def collect():
        active = sublime.active_window()
        windows = []
        for index, window in enumerate(sublime.windows()):
            view = window.active_view()
            windows.append({
                "index": index,
                "id": window.id(),
                "active": active is window,
                "folders": window.folders(),
                "activeViewId": view.id() if view else None,
                "activeViewFile": view.file_name() if view else None,
            })
        return {"windows": windows}
    return run_on_main_thread(collect)


def window_from_params(params):
    windows = sublime.windows()
    if not windows:
        return None
    if params.get("window") == "active" or "window" not in params:
        return sublime.active_window() or windows[0]
    target = params.get("window")
    for window in windows:
        if window.id() == target:
            return window
    if isinstance(target, int) and 0 <= target < len(windows):
        return windows[target]
    return None


def view_summary(view, active=False):
    return {
        "id": view.id(),
        "active": active,
        "fileName": view.file_name(),
        "name": view.name(),
        "isDirty": view.is_dirty(),
        "isScratch": view.is_scratch(),
        "syntax": view.settings().get("syntax"),
        "size": view.size(),
    }


def rpc_list_views(params):
    def collect():
        window = window_from_params(params)
        if not window:
            return {"views": []}
        active = window.active_view()
        return {
            "windowId": window.id(),
            "views": [view_summary(view, view is active) for view in window.views()],
        }
    return run_on_main_thread(collect)


def point_from_params(view, params):
    if "point" in params:
        return max(0, min(int(params["point"]), view.size()))
    if "row" in params or "line" in params:
        row = int(params.get("row", params.get("line", 1)))
        # `line` is treated as 1-based for command-palette friendliness; `row`
        # may be explicitly zero-based by passing row_base=0.
        if int(params.get("row_base", 1)) != 0:
            row -= 1
        col = int(params.get("col", params.get("column", 0)))
        return max(0, min(view.text_point(max(0, row), max(0, col)), view.size()))
    if view.sel():
        return view.sel()[0].begin()
    return 0


def collect_scope_debug(params):
    window = window_from_params(params)
    view = find_view(params)
    if not view:
        raise ValueError("No matching view")

    point = point_from_params(view, params)
    row, col = view.rowcol(point)
    context = int(params.get("context", 8))
    last_row = view.rowcol(view.size())[0]
    lines = []
    for line_no in range(max(0, row - context), min(last_row, row + context) + 1):
        line_region = view.line(view.text_point(line_no, 0))
        text = view.substr(line_region)
        first_non_ws_col = len(text) - len(text.lstrip())
        first_non_ws_point = min(line_region.a + first_non_ws_col, line_region.b)
        lines.append({
            "row": line_no + 1,
            "text": text,
            "lineStartPoint": line_region.a,
            "lineStartScope": view.scope_name(line_region.a),
            "firstNonWhitespaceColumn": first_non_ws_col,
            "firstNonWhitespacePoint": first_non_ws_point,
            "firstNonWhitespaceScope": view.scope_name(first_non_ws_point),
        })

    return {
        "windowId": window.id() if window else None,
        "viewId": view.id(),
        "fileName": view.file_name(),
        "syntax": view.settings().get("syntax"),
        "point": point,
        "cursor": {
            "row": row + 1,
            "col": col,
            "scope": view.scope_name(point),
        },
        "lines": lines,
    }


def rpc_scope_debug(params):
    return run_on_main_thread(lambda: collect_scope_debug(params))


def command_allowed(command):
    if not settings().get("allow_run_command", True):
        return False
    allowlist = settings().get("command_allowlist", []) or []
    return not allowlist or command in allowlist


def rpc_run_window_command(params):
    command = params.get("command")
    args = params.get("args") or {}
    if not command or not isinstance(command, str):
        raise ValueError("command is required")
    if not command_allowed(command):
        raise PermissionError("Command is not allowed: {}".format(command))

    def run():
        window = window_from_params(params)
        if not window:
            raise ValueError("No matching window")
        window.run_command(command, args)
        return {"ok": True, "windowId": window.id(), "command": command}
    return run_on_main_thread(run)


def find_view(params):
    view_id = params.get("view") or params.get("viewId")
    window = window_from_params(params)
    candidates = []
    if window:
        candidates.extend(window.views())
    else:
        for win in sublime.windows():
            candidates.extend(win.views())
    if view_id is None and window:
        return window.active_view()
    for view in candidates:
        if view.id() == view_id:
            return view
    return None


def rpc_run_text_command(params):
    command = params.get("command")
    args = params.get("args") or {}
    if not command or not isinstance(command, str):
        raise ValueError("command is required")
    if not command_allowed(command):
        raise PermissionError("Command is not allowed: {}".format(command))

    def run():
        view = find_view(params)
        if not view:
            raise ValueError("No matching view")
        view.run_command(command, args)
        return {"ok": True, "viewId": view.id(), "command": command}
    return run_on_main_thread(run)


def collect_output_panel(params):
    panel_name = params.get("panel")
    if not panel_name or not isinstance(panel_name, str):
        raise ValueError("panel is required")
    window = window_from_params(params)
    if not window:
        raise ValueError("No matching window")
    view = window.find_output_panel(panel_name)
    if not view:
        return {"found": False, "panel": panel_name}
    text = view.substr(sublime.Region(0, view.size()))
    return dict({"found": True, "panel": panel_name, "viewId": view.id()}, **truncate_text(text))


def rpc_get_output_panel(params):
    return run_on_main_thread(lambda: collect_output_panel(params))


def output_panel_key(panel_name):
    prefix = "output."
    if panel_name.startswith(prefix):
        return panel_name[len(prefix):]
    return panel_name


def rpc_list_output_panels(params):
    def collect():
        window = window_from_params(params)
        if not window:
            raise ValueError("No matching window")
        panels = []
        for panel_name in window.panels():
            panel_key = output_panel_key(panel_name)
            panel = window.find_output_panel(panel_key)
            panels.append({
                "name": panel_name,
                "panel": panel_key,
                "viewId": panel.id() if panel else None,
                "size": panel.size() if panel else None,
            })
        return {"windowId": window.id(), "activePanel": window.active_panel(), "panels": panels}
    return run_on_main_thread(collect)


RPC_METHODS = {
    "ping": rpc_ping,
    "status": rpc_status,
    "list_windows": rpc_list_windows,
    "list_views": rpc_list_views,
    "scope_debug": rpc_scope_debug,
    "run_window_command": rpc_run_window_command,
    "run_text_command": rpc_run_text_command,
    "get_output_panel": rpc_get_output_panel,
    "list_output_panels": rpc_list_output_panels,
    "get_console_log": rpc_get_console_log,
}


def handle_rpc_request(request, token):
    if request.get("token") != token:
        raise PermissionError("unauthorized")
    refresh_idle_deadline()
    method = request.get("method")
    params = request.get("params") or {}
    request_id = request.get("id")
    if method not in RPC_METHODS:
        raise ValueError("Unknown method: {}".format(method))
    return {"id": request_id, "ok": True, "result": RPC_METHODS[method](params)}


class UnixRpcHandler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            raw = self.rfile.readline(1024 * 1024).decode("utf-8")
            response = handle_rpc_request(json.loads(raw) if raw else {}, self.server.bridge_token)
        except Exception as e:
            response = {"ok": False, "error": str(e), "traceback": traceback.format_exc()}
        self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))


class ThreadingUnixStreamServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class BridgeServer:
    def __init__(self):
        cfg = settings()
        self.socket_path = os.path.expanduser(cfg.get("socket_path") or DEFAULT_SOCKET_PATH)
        self.token = secrets.token_urlsafe(32)
        os.makedirs(os.path.dirname(self.socket_path), exist_ok=True)
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        self.server = ThreadingUnixStreamServer(self.socket_path, UnixRpcHandler)
        self.server.bridge_token = self.token
        try:
            os.chmod(self.socket_path, 0o600)
        except OSError:
            pass
        self.endpoint = self.socket_path
        self.thread = threading.Thread(target=self.server.serve_forever, name="SublimeAgentBridge", daemon=True)

    def start(self):
        install_console_log_hook()
        self.thread.start()
        refresh_idle_deadline()
        data = {
            "socketPath": self.socket_path,
            "token": self.token,
            "pid": os.getpid(),
            "package": PACKAGE,
            "name": DISPLAY_NAME,
            "protocolVersion": BRIDGE_PROTOCOL_VERSION,
            "sublimeVersion": sublime.version(),
        }
        write_connection_file(data)
        sublime.status_message("{} listening on {}".format(DISPLAY_NAME, self.endpoint))

    def stop(self, clear_state=True):
        try:
            uninstall_console_log_hook()
            self.server.shutdown()
            self.server.server_close()
            try:
                os.unlink(self.socket_path)
            except FileNotFoundError:
                pass
        finally:
            remove_connection_file()
            if clear_state:
                clear_bridge_state()
            sublime.status_message("{} stopped".format(DISPLAY_NAME))


def get_server():
    with _server_lock:
        return _server


def start_server():
    global _server
    with _server_lock:
        if _server is not None:
            return _server
        try:
            _server = BridgeServer()
            _server.start()
            log("started {}".format(_server.endpoint))
            return _server
        except Exception:
            uninstall_console_log_hook()
            _server = None
            log("start failed:\n" + traceback.format_exc())
            raise


def stop_server(clear_state=True):
    global _server
    with _server_lock:
        server = _server
        _server = None
    if server is not None:
        server.stop(clear_state=clear_state)
    elif clear_state:
        clear_bridge_state()
        remove_connection_file()


class SublimeAgentBridgeStartCommand(sublime_plugin.ApplicationCommand):
    def run(self):
        start_server()


class SublimeAgentBridgeStopCommand(sublime_plugin.ApplicationCommand):
    def run(self):
        stop_server()


class SublimeAgentBridgeStatusCommand(sublime_plugin.ApplicationCommand):
    def run(self):
        server = get_server()
        active_until = read_active_until()
        remaining = max(0, int(active_until - time.time()))
        if server:
            sublime.message_dialog(
                "{} is running\n\n{}\n\nIdle timeout: {} seconds remaining\n\nDiscovery:\n{}".format(
                    DISPLAY_NAME, server.endpoint, remaining, CONNECTION_FILE
                )
            )
        else:
            sublime.message_dialog(
                "{} is stopped\n\nSaved idle deadline: {} seconds remaining\n\nDiscovery:\n{}".format(
                    DISPLAY_NAME, remaining, CONNECTION_FILE
                )
            )


class SublimeAgentBridgeScopeDebugCommand(sublime_plugin.WindowCommand):
    def run(self, context=8):
        data = collect_scope_debug({"window": self.window.id(), "context": context})
        panel = self.window.create_output_panel("agent_bridge_scope_debug")
        panel.run_command("append", {"characters": json.dumps(data, indent=2), "force": True, "scroll_to_end": False})
        self.window.run_command("show_panel", {"panel": "output.agent_bridge_scope_debug"})


def plugin_loaded():
    log("plugin_loaded")
    active_until = read_active_until()
    if active_until > time.time():
        start_server()
    else:
        clear_bridge_state()
        remove_connection_file()


def plugin_unloaded():
    log("plugin_unloaded")
    stop_server(clear_state=False)
