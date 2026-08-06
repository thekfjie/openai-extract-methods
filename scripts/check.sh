#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3)"
fi

git diff --check
bash -n scripts/*.sh

while IFS= read -r -d '' file; do
  case "$file" in refs/*) continue ;; esac
  [ -f "$file" ] || continue
  "$PYTHON_BIN" -m py_compile "$file"
done < <(git ls-files -z '*.py')

"$PYTHON_BIN" -m json.tool config.example.json >/dev/null
"$PYTHON_BIN" -m json.tool frontend/docs/endpoints.json >/dev/null
"$PYTHON_BIN" -m json.tool frontend/docs/backend-routes.json >/dev/null
node scripts/check-frontend.mjs
(cd fingerprint/sdk && npm test)
"$PYTHON_BIN" -m unittest discover -s tests -v

if command -v go >/dev/null 2>&1; then
  go test ./cmd/fingerprint-api ./cmd/control-api ./cmd/extract-api ./internal/fingerprintconfig ./internal/fingerprintmodel ./internal/fingerprintpolicy ./internal/fingerprintsdk ./internal/roxyopenapi ./internal/taskqueue ./internal/controlapi ./internal/extractapi ./internal/extractmethods
elif command -v docker >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
  sudo -n docker run --rm \
    -v "$ROOT_DIR:/src:ro" -w /src golang:1.26-bookworm \
    go test ./cmd/fingerprint-api ./cmd/control-api ./cmd/extract-api ./internal/fingerprintconfig ./internal/fingerprintmodel ./internal/fingerprintpolicy ./internal/fingerprintsdk ./internal/roxyopenapi ./internal/taskqueue ./internal/controlapi ./internal/extractapi ./internal/extractmethods
else
  echo "Go toolchain unavailable; fingerprint API Go tests were not run" >&2
  exit 1
fi

# shellcheck disable=SC1091
source config/ports.env
"$PYTHON_BIN" - "$AUTOMYAI_PORT" "$VNC_PORT" "$NOVNC_PORT" "$FLARESOLVERR_PORT" \
  "$OPENAI2_PORT" "$OPENAI3_PORT" "$OPENAI3_MAIL_PORT" "$OPENAI4_PORT" "$OPENAI5_PORT" "$EXTRACT_API_PORT" "$PAYPAL_PROTOCOL_PORT" "$CARD_PAYMENT_PORT" "$FINGERPRINT_API_PORT" <<'PY'
import sys

ports = [int(value) for value in sys.argv[1:]]
if len(ports) != len(set(ports)):
    raise SystemExit("project-owned ports must be unique")
invalid = [port for port in ports if not 10000 <= port <= 65535]
if invalid:
    raise SystemExit(f"project-owned ports must be high ports: {invalid}")
PY

forbidden_pattern='(^|/)(config\.json$|data/|logs/|node_modules/|\.venv/|__pycache__/)|\.pyc$|\.bak($|[-_.])|mail_credentials\.txt$'
if git ls-files | grep -E "$forbidden_pattern"; then
  echo "Forbidden generated, secret or backup files are tracked" >&2
  exit 1
fi
if git ls-files | grep -E '(^|/)\.env($|\.)' | grep -v '^\.env\.example$'; then
  echo "Environment files other than .env.example must not be tracked" >&2
  exit 1
fi

if command -v docker >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
  sudo -n docker compose --env-file config/ports.env --env-file .env \
    --project-name automyai -f docker-compose.yml config --quiet
fi

echo "All checks passed"
