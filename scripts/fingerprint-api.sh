#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/config/ports.env"

COMPOSE="$ROOT_DIR/scripts/automyai-compose.sh"
SDK_DIR="$ROOT_DIR/fingerprint/sdk"
STATE_DIR="$ROOT_DIR/data/fingerprint-api"
KEY_FILE="$STATE_DIR/api.key"
ROXY_STATE_DIR="$ROOT_DIR/data/roxy-openapi"
ROXY_KEY_FILE="$ROXY_STATE_DIR/api.key"
DEFAULT_PROFILE_FILE="$STATE_DIR/fingerprint.json"
API_URL="${OAI_FINGERPRINT_API_URL:-http://127.0.0.1:${FINGERPRINT_API_PORT}}"

usage() {
  cat >&2 <<EOF
Usage: $0 {start|status|source-status|logs|set-key|test-key|set-roxy-key|roxy-status|presets|generate [PRESET] [SEED] [OUT]}

This controls only the lightweight fingerprint API. It never launches an
Electron application, browser window, QEMU helper, or roxynet process.
EOF
  exit 2
}

container_exec() {
  "$COMPOSE" exec -T "$@"
}

ensure_container() {
  if ! container_exec fingerprint-api true >/dev/null 2>&1; then
    echo "Fingerprint API container is not running. Start it with: $COMPOSE up fingerprint-api" >&2
    exit 1
  fi
}

start_api() {
  ensure_container
  if curl -fsS --max-time 3 "$API_URL/health" >/dev/null 2>&1; then
    echo "Fingerprint API is running on $API_URL"
    return 0
  fi
  echo "Restarting only the fingerprint API..."
  "$COMPOSE" restart fingerprint-api >/dev/null
  local attempt
  for attempt in $(seq 1 20); do
    if curl -fsS --max-time 2 "$API_URL/health" >/dev/null 2>&1; then
      echo "Fingerprint API started on $API_URL"
      return 0
    fi
    sleep 0.5
  done
  echo "Fingerprint API failed to start. Check '$0 logs'." >&2
  exit 1
}

show_status() {
  ensure_container
  if curl -fsS --max-time 2 "$API_URL/health" >/dev/null 2>&1; then
    echo "fingerprint API: listening on $API_URL"
  else
    echo "fingerprint API: not listening"
    return 1
  fi
  if container_exec fingerprint-api sh -lc \
    'pgrep -x roxybrowser >/dev/null 2>&1 || ps -eo comm=,args= | awk '\''$1 ~ /^qemu-x86_64/ && $0 ~ /roxynet/ {found=1} END {exit !found}'\'''; then
    echo "unexpected browser helper process detected" >&2
    return 1
  fi
  echo "browser program: absent"
  echo "SDK: $SDK_DIR"
  echo "state: $STATE_DIR"
}

show_logs() {
  ensure_container
  "$COMPOSE" logs --tail 200 fingerprint-api
}

set_key() {
  local key
  mkdir -p "$STATE_DIR"
  chmod 700 "$STATE_DIR"
  read -r -s -p "Fingerprint API key: " key
  echo >&2
  if [ -z "$key" ]; then
    echo "Key was empty; nothing changed." >&2
    exit 1
  fi
  umask 077
  printf '%s\n' "$key" >"$KEY_FILE"
  chmod 600 "$KEY_FILE"
  unset key
  echo "Key saved at $KEY_FILE"
}

set_roxy_key() {
  local key
  mkdir -p "$ROXY_STATE_DIR"
  chmod 700 "$ROXY_STATE_DIR"
  read -r -s -p "Roxy OpenAPI key: " key
  echo >&2
  if [ -z "$key" ]; then
    echo "Key was empty; nothing changed." >&2
    exit 1
  fi
  umask 077
  printf '%s\n' "$key" >"$ROXY_KEY_FILE"
  chmod 600 "$ROXY_KEY_FILE"
  unset key
  echo "Roxy OpenAPI key saved at $ROXY_KEY_FILE"
}

api_request() {
  local method="$1"
  local path="$2"
  if [ ! -s "$KEY_FILE" ]; then
    echo "No API key file: $KEY_FILE" >&2
    exit 1
  fi
  python3 - "$API_URL" "$KEY_FILE" "$method" "$path" <<'PY'
import json
import sys
import urllib.request

base_url, key_file, method, path = sys.argv[1:]
key = open(key_file, encoding="utf-8").read().strip()
request = urllib.request.Request(
    f"{base_url.rstrip('/')}{path}",
    headers={"token": key, "accept": "application/json"},
    method=method,
)
with urllib.request.urlopen(request, timeout=15) as response:
    payload = json.load(response)
if str(payload.get("code", 0)) not in {"0", "200"}:
    raise SystemExit(payload.get("msg") or "fingerprint API failed")
print(json.dumps(payload.get("data"), ensure_ascii=False, indent=2))
PY
}

test_key() {
  api_request GET /browser/workspace >/dev/null
  echo "Fingerprint API key accepted."
}

source_status() {
  curl -fsS --max-time 5 "$API_URL/health" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin).get("data", {}), ensure_ascii=False, indent=2))'
}

roxy_status() {
  api_request GET /roxy/openapi/status
}

list_presets() {
  api_request GET /fingerprint/presets
}

generate_profile() {
  local preset="${1:-windows-11-chrome}"
  local seed="${2:-}"
  local output="${3:-$DEFAULT_PROFILE_FILE}"
  local temporary
  mkdir -p "$(dirname "$output")"
  temporary="$(mktemp "$(dirname "$output")/.fingerprint.XXXXXX")"
  chmod 600 "$temporary"
  if ! python3 - "$API_URL" "$KEY_FILE" "$preset" "$seed" >"$temporary" <<'PY'
import json
import sys
import urllib.request

base_url, key_file, preset, seed = sys.argv[1:]
key = open(key_file, encoding="utf-8").read().strip()
body = {"preset": preset}
if seed:
    body["seed"] = seed
request = urllib.request.Request(
    f"{base_url.rstrip('/')}/fingerprint/generate",
    data=json.dumps(body).encode("utf-8"),
    headers={"token": key, "content-type": "application/json", "accept": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=30) as response:
    payload = json.load(response)
if str(payload.get("code", 0)) not in {"0", "200"}:
    raise SystemExit(payload.get("msg") or "fingerprint API failed")
bundle = payload.get("data")
if not isinstance(bundle, dict):
    raise SystemExit("fingerprint API returned no bundle")
print(json.dumps(bundle, ensure_ascii=False, indent=2))
PY
  then
    rm -f "$temporary"
    return 1
  fi
  node "$SDK_DIR/cli.mjs" validate "$temporary" >/dev/null
  mv -f "$temporary" "$output"
  chmod 600 "$output"
  python3 - "$output" <<'PY'
import json
import sys

bundle = json.load(open(sys.argv[1], encoding="utf-8"))
profile = bundle["profile"]
roxy = bundle["roxyConfig"]
print(f"Fingerprint generated: {sys.argv[1]}")
print(f"preset={profile['preset']} profile_id={profile['id']}")
print(f"ua={profile['engine']['userAgent']}")
print(f"device_name={roxy.get('computerName', '')}")
PY
}

case "${1:-}" in
  start)
    [ "$#" -eq 1 ] || usage
    start_api
    ;;
  status)
    [ "$#" -eq 1 ] || usage
    show_status
    ;;
  source-status)
    [ "$#" -eq 1 ] || usage
    source_status
    ;;
  logs)
    [ "$#" -eq 1 ] || usage
    show_logs
    ;;
  set-key)
    [ "$#" -eq 1 ] || usage
    set_key
    ;;
  test-key)
    [ "$#" -eq 1 ] || usage
    test_key
    ;;
  set-roxy-key)
    [ "$#" -eq 1 ] || usage
    set_roxy_key
    ;;
  roxy-status)
    [ "$#" -eq 1 ] || usage
    roxy_status
    ;;
  presets)
    [ "$#" -eq 1 ] || usage
    list_presets
    ;;
  generate)
    shift
    [ "$#" -le 3 ] || usage
    generate_profile "${1:-}" "${2:-}" "${3:-}"
    ;;
  *) usage ;;
esac
