#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_DIR="$ROOT_DIR/artifacts/checkout-compatibility-notes-20260806"
# Restore the engine snapshot captured immediately before this compatibility
# change; this preserves unrelated work that landed earlier in the engine.
cp "$ARTIFACT_DIR/original/internal/extractmethods/engine.go" "$ROOT_DIR/internal/extractmethods/engine.go"
# Remove only the compatibility source added by this change.
rm -f "$ROOT_DIR/internal/extractmethods/checkout_compat.go"
# Remove the public-clone filtering hook from jobs.go without overwriting
# unrelated job-manager changes made before this change.
python3 - "$ROOT_DIR/internal/extractmethods/jobs.go" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
s = path.read_text()
s = s.replace('result := cloneJobPublic(job)', 'result := cloneJob(job)')
marker = '// cloneJobPublic is used for API responses.'
if marker in s:
    start = s.index(marker)
    end = s.index('\nfunc durationSince', start)
    s = s[:start] + s[end + 1:]
path.write_text(s)
PY
cp "$ARTIFACT_DIR/original/internal/extractmethods/extractmethods_test.go" "$ROOT_DIR/internal/extractmethods/extractmethods_test.go"
"$ROOT_DIR/scripts/automyai-compose.sh" deploy extract-api
