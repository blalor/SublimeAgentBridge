#!/usr/bin/env bash
set -euo pipefail

method="${1:-ping}"
params="${2:-{}}"
connection_file="$HOME/Library/Application Support/Sublime Text/Cache/Sublime Agent Bridge/connection.json"

if [[ ! -f "$connection_file" ]]; then
  echo "connection file not found: $connection_file" >&2
  echo "Start the bridge in Sublime with: Sublime Agent Bridge: Start Server" >&2
  exit 2
fi

url="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["url"])' "$connection_file")"
token="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["token"])' "$connection_file")"

curl -fsS \
  -H 'content-type: application/json' \
  -H "authorization: Bearer $token" \
  --data "$(python3 -c 'import json,sys; print(json.dumps({"id":"cli","method":sys.argv[1],"params":json.loads(sys.argv[2])}))' "$method" "$params")" \
  "$url/rpc" | python3 -m json.tool
