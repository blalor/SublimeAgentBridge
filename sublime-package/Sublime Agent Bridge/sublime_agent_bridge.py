import json
import os
import secrets
import shutil
import socketserver
import subprocess
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import sublime
import sublime_plugin


PACKAGE = "Sublime Agent Bridge"
SETTINGS = "Sublime Agent Bridge.sublime-settings"
CONNECTION_FILE = os.path.join(sublime.cache_path(), PACKAGE, "connection.json")
LOG_FILE = os.path.join(sublime.cache_path(), PACKAGE, "bridge.log")
DEFAULT_SOCKET_PATH = os.path.join(sublime.cache_path(), PACKAGE, "bridge.sock")

_server = None
_server_lock = threading.RLock()


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


def truncate_text(text):
    limit = max_text_bytes()
    data = text.encode("utf-8")
    if len(data) <= limit:
        return {"text": text, "truncated": False, "bytes": len(data)}
    truncated = data[:limit].decode("utf-8", "replace")
    return {"text": truncated, "truncated": True, "bytes": len(data), "returnedBytes": limit}


def dedupe_path(parts):
    seen = set()
    output = []
    for part in parts:
        if not part:
            continue
        expanded = os.path.expanduser(part)
        if expanded in seen:
            continue
        seen.add(expanded)
        output.append(expanded)
    return output


def split_path(value):
    return [part for part in value.split(os.pathsep) if part]


def write_connection_file(data):
    os.makedirs(os.path.dirname(CONNECTION_FILE), exist_ok=True)
    with open(CONNECTION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    try:
        os.chmod(CONNECTION_FILE, 0o600)
    except OSError:
        pass


def remove_connection_file():
    try:
        os.unlink(CONNECTION_FILE)
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
    return {"ok": True, "package": PACKAGE, "sublimeVersion": sublime.version()}


def rpc_status(_params):
    server = get_server()
    return {
        "running": server is not None,
        "connectionFile": CONNECTION_FILE,
        "transport": server.transport if server else None,
        "url": server.url if server else None,
        "host": server.host if server else None,
        "port": server.port if server else None,
        "socketPath": server.socket_path if server else None,
        "pid": os.getpid(),
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


def window_start_path(window, params=None):
    params = params or {}
    if params.get("path"):
        return params.get("path")
    view = find_view(params) if ("view" in params or "viewId" in params) else None
    if view and view.file_name():
        return view.file_name()
    view = window.active_view() if window else None
    if view and view.file_name():
        return view.file_name()
    folders = window.folders() if window else []
    if folders:
        return folders[0]
    return None


def folder_for_path(window, path):
    folders = window.folders() if window else []
    if path:
        path = os.path.abspath(path)
        if os.path.isfile(path):
            path = os.path.dirname(path)
        matches = [folder for folder in folders if path == folder or path.startswith(folder + os.sep)]
        if matches:
            return max(matches, key=len)
    return folders[0] if folders else None


def find_envrc_dir(start, stop_at=None):
    if not start:
        return None
    path = os.path.abspath(start)
    if os.path.isfile(path):
        path = os.path.dirname(path)
    stop_at = os.path.abspath(stop_at) if stop_at else None
    while True:
        if os.path.isfile(os.path.join(path, ".envrc")):
            return path
        if stop_at and path == stop_at:
            return None
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


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
    panel_name = params.get("panel", "env_doctor")
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


def rpc_env_doctor(params):
    """Run Env Doctor's command, then return its output panel text.

    This intentionally automates the existing command instead of importing its internals.
    """
    def run():
        window = window_from_params(params)
        if not window:
            raise ValueError("No matching window")
        window.run_command("env_doctor")
        return {"ok": True, "windowId": window.id()}
    run_on_main_thread(run)
    return run_on_main_thread(lambda: collect_output_panel({**params, "panel": "env_doctor"}))


def bootstrap_env():
    """Return a clean, deterministic base environment for evaluating direnv.

    Do not inherit Sublime's PATH or already-active direnv/Flox variables: if
    Sublime was launched from an activated terminal, those would contaminate
    unrelated windows. Keep only a small allowlist that environment managers
    commonly need, then provide a configured PATH for finding direnv and other
    bootstrap helpers used by .envrc files.
    """
    env = {}
    for key in settings().get("env_passthrough_vars", []) or []:
        if key in os.environ:
            env[key] = os.environ[key]
    env.setdefault("HOME", os.path.expanduser("~"))
    configured = settings().get("env_bootstrap_path_dirs", []) or []
    env["PATH"] = os.pathsep.join(dedupe_path(configured))
    return env


def find_direnv(env):
    command = settings().get("direnv_command")
    if command:
        expanded = os.path.expanduser(command)
        if os.path.isabs(expanded) and os.path.exists(expanded):
            return expanded
        found = shutil.which(expanded, path=env.get("PATH"))
        if found:
            return found
    return shutil.which("direnv", path=env.get("PATH"))


def collect_environment_context(params):
    def collect():
        window = window_from_params(params)
        if not window:
            raise ValueError("No matching window")
        start = window_start_path(window, params)
        folder = folder_for_path(window, start)
        return {
            "windowId": window.id(),
            "startPath": start,
            "folder": folder,
            "envrcDir": find_envrc_dir(start, folder),
        }
    return run_on_main_thread(collect)


def resolve_environment(params):
    context = collect_environment_context(params)
    env = bootstrap_env()
    direnv = find_direnv(env)
    context["direnv"] = direnv
    context["bootstrapPath"] = split_path(env.get("PATH", ""))
    exported = None
    if direnv and context.get("envrcDir"):
        proc = subprocess.Popen(
            [direnv, "export", "json"],
            cwd=context["envrcDir"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = proc.communicate()
        context["direnvReturncode"] = proc.returncode
        context["direnvStderr"] = stderr.decode("utf-8", "replace")
        if proc.returncode != 0:
            raise RuntimeError("direnv export json failed: " + context["direnvStderr"])
        stdout = stdout.decode("utf-8", "replace")
        exported = json.loads(stdout) if stdout.strip() else {}
        env.update({k: str(v) for k, v in exported.items() if v is not None})
    else:
        context["direnvReturncode"] = None
        context["direnvStderr"] = ""
    return context, env, exported or {}


def rpc_resolve_environment(params):
    context, env, exported = resolve_environment(params)
    interesting = params.get("interestingVars") or settings().get("environment_interesting_vars", []) or []
    result = dict(context)
    result["path"] = split_path(env.get("PATH", ""))
    result["exportedKeys"] = sorted(exported.keys())
    result["vars"] = {key: env[key] for key in interesting if key in env}
    if params.get("includeEnv"):
        result["env"] = env
    tools = params.get("tools") or []
    if tools:
        result["tools"] = {tool: shutil.which(tool, path=env.get("PATH")) for tool in tools}
    return result


def rpc_which(params):
    tools = params.get("tools") or []
    if not isinstance(tools, list) or not all(isinstance(tool, str) for tool in tools):
        raise ValueError("tools must be a list of command names")
    context, env, _exported = resolve_environment(params)
    return dict(context, tools={tool: shutil.which(tool, path=env.get("PATH")) for tool in tools})


RPC_METHODS = {
    "ping": rpc_ping,
    "status": rpc_status,
    "list_windows": rpc_list_windows,
    "list_views": rpc_list_views,
    "run_window_command": rpc_run_window_command,
    "run_text_command": rpc_run_text_command,
    "get_output_panel": rpc_get_output_panel,
    "env_doctor": rpc_env_doctor,
    "resolve_environment": rpc_resolve_environment,
    "which": rpc_which,
}


def handle_rpc_request(request, token):
    if request.get("token") != token:
        raise PermissionError("unauthorized")
    method = request.get("method")
    params = request.get("params") or {}
    request_id = request.get("id")
    if method not in RPC_METHODS:
        raise ValueError("Unknown method: {}".format(method))
    return {"id": request_id, "ok": True, "result": RPC_METHODS[method](params)}


class BridgeRequestHandler(BaseHTTPRequestHandler):
    server_version = "SublimeAgentBridge/0.1"

    def log_message(self, format, *args):
        return

    def do_GET(self):
        if urlparse(self.path).path == "/healthz":
            self.write_json(200, {"ok": True})
        else:
            self.write_json(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/rpc":
            self.write_json(404, {"error": "not found"})
            return
        if self.headers.get("Authorization") != "Bearer " + self.server.bridge_token:
            self.write_json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            response = handle_rpc_request(json.loads(raw) if raw else {}, self.server.bridge_token)
            self.write_json(200, response)
        except Exception as e:
            self.write_json(500, {"ok": False, "error": str(e), "traceback": traceback.format_exc()})

    def write_json(self, status, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


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
        self.transport = cfg.get("transport", "unix")
        self.host = cfg.get("host", "127.0.0.1")
        self.requested_port = int(cfg.get("port", 0))
        self.socket_path = os.path.expanduser(cfg.get("socket_path") or DEFAULT_SOCKET_PATH)
        self.token = secrets.token_urlsafe(32)
        self.port = None
        self.url = None
        if self.transport == "tcp":
            self.server = ThreadingHTTPServer((self.host, self.requested_port), BridgeRequestHandler)
            self.server.bridge_token = self.token
            self.port = self.server.server_address[1]
            self.url = "http://{}:{}".format(self.host, self.port)
            self.endpoint = self.url
        else:
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
        self.thread.start()
        data = {
            "transport": self.transport,
            "token": self.token,
            "pid": os.getpid(),
            "package": PACKAGE,
            "sublimeVersion": sublime.version(),
        }
        if self.transport == "tcp":
            data.update({"url": self.url, "host": self.host, "port": self.port})
        else:
            data.update({"socketPath": self.socket_path})
        write_connection_file(data)
        sublime.status_message("Sublime Agent Bridge listening on {}".format(self.endpoint))

    def stop(self):
        try:
            self.server.shutdown()
            self.server.server_close()
            if self.transport != "tcp":
                try:
                    os.unlink(self.socket_path)
                except FileNotFoundError:
                    pass
        finally:
            remove_connection_file()
            sublime.status_message("Sublime Agent Bridge stopped")


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
            log("start failed:\n" + traceback.format_exc())
            raise


def stop_server():
    global _server
    with _server_lock:
        server = _server
        _server = None
    if server is not None:
        server.stop()


class SublimeAgentBridgeStartCommand(sublime_plugin.ApplicationCommand):
    def run(self):
        start_server()


class SublimeAgentBridgeStopCommand(sublime_plugin.ApplicationCommand):
    def run(self):
        stop_server()


class SublimeAgentBridgeStatusCommand(sublime_plugin.ApplicationCommand):
    def run(self):
        server = get_server()
        if server:
            sublime.message_dialog("Sublime Agent Bridge is running\n\n{}\n\nDiscovery:\n{}".format(server.endpoint, CONNECTION_FILE))
        else:
            sublime.message_dialog("Sublime Agent Bridge is stopped\n\nDiscovery:\n{}".format(CONNECTION_FILE))


def plugin_loaded():
    log("plugin_loaded")
    if settings().get("start_on_load", True):
        start_server()


def plugin_unloaded():
    log("plugin_unloaded")
    stop_server()
