#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORTS_FILE="$ROOT_DIR/config/ports.env"
NGINX_TEMPLATE="$ROOT_DIR/deploy/nginx/automyai.kfjie.me.conf.template"
NGINX_TARGET="/etc/nginx/conf.d/automyai.kfjie.me.conf"
RESTART=false

if [ "${1:-}" = "--restart" ]; then
  RESTART=true
elif [ "$#" -gt 0 ]; then
  echo "Usage: $0 [--restart]" >&2
  exit 2
fi

rendered_nginx="$(mktemp)"
trap 'rm -f "$rendered_nginx"' EXIT

"$ROOT_DIR/scripts/build-go.sh"

python3 - "$PORTS_FILE" "$NGINX_TEMPLATE" "$rendered_nginx" <<'PY'
from pathlib import Path
import re
import sys

ports_path, template_path, output_path = map(Path, sys.argv[1:])
values = {}
for raw_line in ports_path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip()

text = template_path.read_text(encoding="utf-8")
for key, value in values.items():
    text = text.replace(f"@@{key}@@", value)
missing = sorted(set(re.findall(r"@@([A-Z0-9_]+)@@", text)))
if missing:
    raise SystemExit(f"missing port values: {', '.join(missing)}")
output_path.write_text(text, encoding="utf-8")
PY

for unit in "$ROOT_DIR"/deploy/systemd/*.service; do
  sudo -n install -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
sudo -n install -m 0644 "$rendered_nginx" "$NGINX_TARGET"
sudo -n systemctl daemon-reload
sudo -n nginx -t

if [ "$RESTART" = true ]; then
  sudo -n systemctl restart automyai-openai3-mail.service
  sudo -n systemctl restart automyai-openai2.service automyai-openai3.service automyai-openai4.service automyai-openai5.service
  sudo -n systemctl reload nginx
  echo "Runtime configuration installed and services restarted"
else
  echo "Runtime configuration installed; run again with --restart to apply it"
fi
