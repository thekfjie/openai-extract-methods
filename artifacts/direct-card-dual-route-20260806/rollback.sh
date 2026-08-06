#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_DIR="$ROOT_DIR/artifacts/direct-card-dual-route-20260806"
while IFS= read -r -d '' source; do
  relative="${source#"$ARTIFACT_DIR/original/"}"
  mkdir -p "$ROOT_DIR/$(dirname "$relative")"
  cp "$source" "$ROOT_DIR/$relative"
done < <(find "$ARTIFACT_DIR/original" -type f -print0)
rm -f \
  "$ROOT_DIR/.gitattributes" \
  "$ROOT_DIR/internal/extractmethods/direct_checkout_routes.go" \
  "$ROOT_DIR/docs/version-archives/2026-08-06-direct-card-dual-route.md"
if [ "${1:-}" = "--deploy" ]; then
  "$ROOT_DIR/scripts/automyai-compose.sh" deploy extract-api
fi
