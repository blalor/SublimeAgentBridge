#!/usr/bin/env bash
set -euo pipefail

method="${1:-ping}"
params="${2:-{}}"
connection_file="$HOME/Library/Caches/Sublime Text/Cache/Sublime Agent Bridge/connection.json"

if [[ ! -f "$connection_file" ]]; then
  echo "connection file not found: $connection_file" >&2
  echo "Start the bridge in Sublime with: Sublime Agent Bridge: Start Server" >&2
  exit 2
fi

python3 - "$connection_file" "$method" "$params" <<'PY'
import http.client
import json
import socket
import sys
import urllib.parse

connection_file, method, params = sys.argv[1:]
conn = json.load(open(connection_file))
payload = json.dumps({
    "id": "cli",
    "method": method,
    "params": json.loads(params),
    "token": conn["token"],
}).encode()

if conn.get("transport") == "unix" or conn.get("socketPath"):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(conn["socketPath"])
    sock.sendall(payload + b"\n")
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    sock.close()
    print(json.dumps(json.loads(data.decode()), indent=2, sort_keys=True))
else:
    url = urllib.parse.urlparse(conn["url"])
    http = http.client.HTTPConnection(url.hostname, url.port)
    http.request("POST", "/rpc", body=payload, headers={
        "content-type": "application/json",
        "authorization": "Bearer " + conn["token"],
    })
    response = http.getresponse()
    print(json.dumps(json.loads(response.read().decode()), indent=2, sort_keys=True))
PY
