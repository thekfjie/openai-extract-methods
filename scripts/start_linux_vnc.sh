#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DISPLAY_NUM="${DISPLAY_NUM:-1}"
DISPLAY_VALUE=":${DISPLAY_NUM}"
VNC_PORT="${VNC_PORT:-15901}"
NOVNC_PORT="${NOVNC_PORT:-16080}"
FINGERPRINT_API_PORT="${FINGERPRINT_API_PORT:?FINGERPRINT_API_PORT must come from config/ports.env}"
EXTRACT_API_PORT="${EXTRACT_API_PORT:?EXTRACT_API_PORT must come from config/ports.env}"
VNC_RESOLUTION="${VNC_RESOLUTION:-1280x800x24}"
PORT_WAIT_SECONDS="${PORT_WAIT_SECONDS:-5}"
VNC_ENABLED="${VNC_ENABLED:-true}"
EMBEDDED_FINGERPRINT_API="${EMBEDDED_FINGERPRINT_API:-false}"
EMBEDDED_EXTRACT_API="${EMBEDDED_EXTRACT_API:-false}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing command: $1" >&2
    return 1
  fi
}

require_cmd python3

read_server_setting() {
  local key="$1"
  local fallback="$2"
  python3 - "$ROOT_DIR/config.json" "$key" "$fallback" <<'PY'
import json
import sys

path, key, fallback = sys.argv[1:]
try:
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle).get(key, fallback)
except (OSError, ValueError, AttributeError):
    value = fallback
print(value)
PY
}

SERVER_HOST="${AUTOMYAI_HOST:-$(read_server_setting HOST 127.0.0.1)}"
SERVER_PORT="${AUTOMYAI_PORT:-$(read_server_setting PORT 13030)}"

validate_port() {
  local name="$1"
  local port="$2"
  if [[ ! "$port" =~ ^[0-9]+$ ]] || ((port < 1 || port > 65535)); then
    echo "Invalid ${name}: ${port}" >&2
    exit 2
  fi
}

validate_port SERVER_PORT "$SERVER_PORT"
validate_port VNC_PORT "$VNC_PORT"
validate_port NOVNC_PORT "$NOVNC_PORT"
validate_port FINGERPRINT_API_PORT "$FINGERPRINT_API_PORT"
validate_port EXTRACT_API_PORT "$EXTRACT_API_PORT"

if [ "$SERVER_PORT" = "$VNC_PORT" ] || [ "$SERVER_PORT" = "$NOVNC_PORT" ] || [ "$VNC_PORT" = "$NOVNC_PORT" ]; then
  echo "SERVER_PORT, VNC_PORT and NOVNC_PORT must be different" >&2
  exit 2
fi

if [ "$FINGERPRINT_API_PORT" = "$SERVER_PORT" ] || \
   [ "$FINGERPRINT_API_PORT" = "$VNC_PORT" ] || \
   [ "$FINGERPRINT_API_PORT" = "$NOVNC_PORT" ]; then
  echo "FINGERPRINT_API_PORT must be distinct from server and VNC ports" >&2
  exit 2
fi

if [ "$EXTRACT_API_PORT" = "$SERVER_PORT" ] || \
   [ "$EXTRACT_API_PORT" = "$VNC_PORT" ] || \
   [ "$EXTRACT_API_PORT" = "$NOVNC_PORT" ] || \
   [ "$EXTRACT_API_PORT" = "$FINGERPRINT_API_PORT" ]; then
  echo "EXTRACT_API_PORT must be distinct from server, VNC and fingerprint ports" >&2
  exit 2
fi

port_available() {
  local host="$1"
  local port="$2"
  python3 - "$host" "$port" <<'PY'
import socket
import sys

host, raw_port = sys.argv[1:]
port = int(raw_port)
family = socket.AF_INET6 if ":" in host else socket.AF_INET
sock = socket.socket(family, socket.SOCK_STREAM)
try:
    sock.bind((host, port))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

wait_for_port() {
  local label="$1"
  local host="$2"
  local port="$3"
  local announced=false
  while ! port_available "$host" "$port"; do
    if [ "$announced" = false ]; then
      echo "${label} port ${host}:${port} is already occupied; waiting instead of restarting" >&2
      announced=true
    fi
    sleep "$PORT_WAIT_SECONDS"
  done
  if [ "$announced" = true ]; then
    echo "${label} port ${host}:${port} is free; continuing startup"
  fi
}

# Do not create the desktop/process tree while another server still owns the
# main port. This avoids Docker restart storms and piles of short-lived VNC
# processes during a stale-process or duplicate-deployment incident.
wait_for_port "automyai" "$SERVER_HOST" "$SERVER_PORT"

export DISPLAY="${BROWSER_DISPLAY:-$DISPLAY_VALUE}"
export BROWSER_DISPLAY="$DISPLAY"
DISPLAY_ID="${DISPLAY#:}"
CHILD_PIDS=()
SERVER_PID=""

start_background_logged() {
  local log_path="$1"
  shift
  "$@" >>"$log_path" 2>&1 &
  CHILD_PIDS+=("$!")
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  for pid in "${CHILD_PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  for pid in "${CHILD_PIDS[@]:-}"; do
    wait "$pid" 2>/dev/null || true
  done
  rm -f "/tmp/.X${DISPLAY_ID}-lock" "/tmp/.X11-unix/X${DISPLAY_ID}" 2>/dev/null || true
  exit "$status"
}

trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

if [ "$VNC_ENABLED" = "true" ]; then
  require_cmd Xvfb
  require_cmd x11vnc

  if ! port_available 127.0.0.1 "$VNC_PORT" || ! port_available 127.0.0.1 "$NOVNC_PORT"; then
    echo "VNC/noVNC port is occupied; disabling desktop while keeping the API available" >&2
    VNC_ENABLED=false
  fi
fi

if [ "$EMBEDDED_FINGERPRINT_API" = "true" ]; then
  wait_for_port "fingerprint API" 127.0.0.1 "$FINGERPRINT_API_PORT"
  start_background_logged /tmp/automyai_fingerprint_api.log \
    /usr/local/bin/automyai-fingerprint-api \
      --host 127.0.0.1 --port "$FINGERPRINT_API_PORT" \
      --key-file /app/data/fingerprint-api/api.key \
      --config /app/config.json
fi

if [ "$EMBEDDED_EXTRACT_API" = "true" ]; then
  wait_for_port "Go extraction API" 127.0.0.1 "$EXTRACT_API_PORT"
  start_background_logged /tmp/automyai_extract_api.log \
    /usr/local/bin/automyai-extract-api \
      --host 127.0.0.1 --port "$EXTRACT_API_PORT" \
      --config /app/config.json --data /app/data/extract-api/jobs.json
fi

if [ "$VNC_ENABLED" = "true" ]; then

  rm -f "/tmp/.X${DISPLAY_ID}-lock" "/tmp/.X11-unix/X${DISPLAY_ID}"
  start_background_logged /tmp/automyai_xvfb.log \
    Xvfb "$DISPLAY" -screen 0 "$VNC_RESOLUTION" -ac +extension GLX +render -noreset
  sleep 1

  if command -v openbox >/dev/null 2>&1; then
    start_background_logged /tmp/automyai_openbox.log env DISPLAY="$DISPLAY" openbox
  elif command -v fluxbox >/dev/null 2>&1; then
    start_background_logged /tmp/automyai_fluxbox.log env DISPLAY="$DISPLAY" fluxbox
  fi

  # Keep x11vnc in the foreground and supervised by this script. Its old -bg
  # mode daemonized, which made cleanup and duplicate-process diagnosis harder.
  start_background_logged /tmp/automyai_x11vnc.stdout.log \
    x11vnc -display "$DISPLAY" -rfbport "$VNC_PORT" -localhost -forever -shared -nopw -noxdamage \
    -fixscreen X=2 \
    -o /tmp/automyai_x11vnc.log

  NOVNC_WEB_ROOT=""
  for candidate in /usr/share/novnc /usr/share/novnc/html /opt/novnc; do
    if [ -d "$candidate" ]; then
      NOVNC_WEB_ROOT="$candidate"
      break
    fi
  done

  if [ -n "$NOVNC_WEB_ROOT" ]; then
    start_background_logged /tmp/automyai_novnc.log \
      /usr/local/bin/novnc-websockify --web="$NOVNC_WEB_ROOT" \
      "127.0.0.1:${NOVNC_PORT}" "127.0.0.1:${VNC_PORT}"
  else
    echo "noVNC web root not found; web VNC disabled" >&2
  fi

  if command -v xsetroot >/dev/null 2>&1; then
    DISPLAY="$DISPLAY" xsetroot -solid "#1f3a32" >/dev/null 2>&1 || true
  fi
  if command -v xterm >/dev/null 2>&1; then
    start_background_logged /tmp/automyai_xterm.log \
      env DISPLAY="$DISPLAY" xterm -geometry 90x20+40+40 -fa "DejaVu Sans Mono" -fs 10 \
      -bg "#10241e" -fg "#d8f0e4" -T "automyai-desktop" -e bash -lc \
      'echo "automyai Xvfb desktop online"; echo "Start a browser task to open Chromium here."; while true; do sleep 3600; done'
  fi
fi

export VNC_WEB_URL="${VNC_WEB_URL:-https://automyai.kfjie.me/novnc/vnc.html?autoconnect=1&resize=scale&path=novnc/websockify}"

echo "automyai: ${SERVER_HOST}:${SERVER_PORT}"
echo "DISPLAY=$DISPLAY"
if [ "$VNC_ENABLED" = "true" ]; then
  echo "VNC: 127.0.0.1:${VNC_PORT}"
  echo "noVNC: ${VNC_WEB_URL}"
fi

cd "$ROOT_DIR"
python3 server.py &
SERVER_PID="$!"
CHILD_PIDS+=("$SERVER_PID")

set +e
wait "$SERVER_PID"
status=$?
set -e
exit "$status"
