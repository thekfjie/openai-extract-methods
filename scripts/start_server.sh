#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_HOST="${AUTOMYAI_HOST:-127.0.0.1}"
SERVER_PORT="${AUTOMYAI_PORT:?AUTOMYAI_PORT must come from config/ports.env}"

python3 - "$SERVER_HOST" "$SERVER_PORT" <<'PY'
import socket
import sys

host, raw_port = sys.argv[1:]
port = int(raw_port)
if not 1 <= port <= 65535:
    raise SystemExit(f"invalid AUTOMYAI_PORT: {port}")
family = socket.AF_INET6 if ":" in host else socket.AF_INET
with socket.socket(family, socket.SOCK_STREAM) as sock:
    # Match the HTTP server's reusable-listener behavior.  Without this, a
    # normal container recreate can mistake the previous listener's TIME_WAIT
    # sockets for another live process and enter a restart loop.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as error:
        raise SystemExit(f"automyai cannot bind {host}:{port}: {error}") from error
PY

export DISPLAY="${BROWSER_DISPLAY:-:1}"
export BROWSER_DISPLAY="$DISPLAY"
export VNC_WEB_URL="${VNC_WEB_URL:-https://automyai.kfjie.me/novnc/vnc.html?autoconnect=1&resize=scale&path=novnc/websockify}"

cd "$ROOT_DIR"
exec python3 server.py
