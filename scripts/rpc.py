#!/usr/bin/env python3
"""Standalone manual diagnostic client.

The Pi extension must not call this script. Extension RPC traffic must go through
pi-extension/sublime-bridge.ts:callBridge().
"""

import argparse
import json
import os
import socket
import sys


CONNECTION_FILES = [
    os.path.expanduser("~/Library/Caches/Sublime Text/Cache/Agent Bridge/connection.json"),
    # Legacy package name used by earlier versions of the Sublime package.
    os.path.expanduser("~/Library/Caches/Sublime Text/Cache/Sublime Agent Bridge/connection.json"),
]


def find_connection_file():
    for path in CONNECTION_FILES:
        if os.path.isfile(path):
            return path
    return None


def read_connection(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def call_unix_socket(socket_path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(socket_path)
        sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data.decode("utf-8"))


def parse_args():
    parser = argparse.ArgumentParser(description="Diagnostic client for the Sublime Agent Bridge JSON-RPC API.")
    parser.add_argument("method", nargs="?", default="ping", help="RPC method to call, default: ping")
    parser.add_argument("params", nargs="?", default="{}", help="JSON object of RPC parameters, default: {}")
    parser.add_argument("--connection-file", help="Path to connection.json")
    return parser.parse_args()


def main():
    args = parse_args()
    connection_file = args.connection_file or find_connection_file()
    if not connection_file:
        print("connection file not found: {}".format(" ".join(CONNECTION_FILES)), file=sys.stderr)
        print("Start the bridge in Sublime with: Sublime Agent Bridge: Start Server", file=sys.stderr)
        return 2

    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as e:
        print("invalid params JSON: {}".format(e), file=sys.stderr)
        return 2
    if not isinstance(params, dict):
        print("params must be a JSON object", file=sys.stderr)
        return 2

    conn = read_connection(connection_file)
    payload = {
        "id": "cli",
        "method": args.method,
        "params": params,
        "token": conn["token"],
    }

    if not conn.get("socketPath"):
        print("connection file does not contain socketPath", file=sys.stderr)
        return 2
    result = call_unix_socket(conn["socketPath"], payload)

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
