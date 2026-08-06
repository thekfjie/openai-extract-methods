#!/usr/bin/env bash
set -euo pipefail

DISPLAY_NUM="${DISPLAY_NUM:-1}"
DISPLAY_VALUE=":${DISPLAY_NUM}"
DISPLAY="${BROWSER_DISPLAY:-$DISPLAY_VALUE}"
DISPLAY_ID="${DISPLAY#:}"
VNC_PORT="${VNC_PORT:?VNC_PORT must come from config/ports.env}"
NOVNC_PORT="${NOVNC_PORT:?NOVNC_PORT must come from config/ports.env}"
VNC_RESOLUTION="${VNC_RESOLUTION:-1280x800x24}"
CHILD_PIDS=()

export DISPLAY
export BROWSER_DISPLAY="$DISPLAY"

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
    kill "$pid" 2>/dev/null || true
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

rm -f "/tmp/.X${DISPLAY_ID}-lock" "/tmp/.X11-unix/X${DISPLAY_ID}"
start_background_logged /tmp/automyai_xvfb.log \
  Xvfb "$DISPLAY" -screen 0 "$VNC_RESOLUTION" -ac +extension GLX +render -noreset

for _ in $(seq 1 50); do
  [ -S "/tmp/.X11-unix/X${DISPLAY_ID}" ] && break
  sleep 0.1
done
if [ ! -S "/tmp/.X11-unix/X${DISPLAY_ID}" ]; then
  echo "Xvfb did not create display socket $DISPLAY" >&2
  exit 1
fi

if command -v fluxbox >/dev/null 2>&1; then
  start_background_logged /tmp/automyai_fluxbox.log \
    fluxbox -rc /app/deploy/desktop/fluxbox-init
fi

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
if [ -z "$NOVNC_WEB_ROOT" ]; then
  echo "noVNC web root not found" >&2
  exit 1
fi

# Paint a deterministic root frame so noVNC receives visible pixels immediately,
# even before Chromium creates a window.  -noxdamage above also forces x11vnc to
# send the initial framebuffer instead of waiting for a damage event.
if command -v xsetroot >/dev/null 2>&1; then
  DISPLAY="$DISPLAY" xsetroot -solid "#17221e" >/dev/null 2>&1 || true
fi

# Show an explicit healthy idle frame. It is covered automatically when the
# headed browser opens, and avoids confusing an empty desktop with a dead VNC.
if command -v xterm >/dev/null 2>&1; then
  start_background_logged /tmp/automyai_desktop_ready.log \
    xterm -display "$DISPLAY" -geometry 78x12+70+70 -title "AutoMyAI Desktop Ready" \
      -bg "#17221e" -fg "#e8f4ee" -fa "DejaVu Sans Mono" -fs 12 \
      -e sh -lc 'printf "\n  AutoMyAI desktop is connected.\n\n  Waiting for the headed browser to start...\n"; exec tail -f /dev/null'
fi

start_background_logged /tmp/automyai_novnc.log \
  websockify --web="$NOVNC_WEB_ROOT" \
  "127.0.0.1:${NOVNC_PORT}" "127.0.0.1:${VNC_PORT}"

echo "automyai desktop: DISPLAY=$DISPLAY VNC=127.0.0.1:${VNC_PORT} noVNC=127.0.0.1:${NOVNC_PORT}"
wait -n "${CHILD_PIDS[@]}"
