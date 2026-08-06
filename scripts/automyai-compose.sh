#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.yml"
PORTS_FILE="$ROOT_DIR/config/ports.env"
SECRETS_FILE="$ROOT_DIR/.env"
LOCK_FILE="${AUTOMYAI_COMPOSE_LOCK:-/tmp/automyai-compose.lock}"
PROJECT_NAME="automyai"
CORE_SERVICES=(automyai desktop extract-api fingerprint-api paypal-protocol card-payment-portal automyai-flaresolverr)

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Another automyai compose operation is already running" >&2
  exit 1
fi

if [ "$(id -u)" -eq 0 ]; then
  SYSTEM_DOCKER=(docker --host unix:///var/run/docker.sock)
else
  SYSTEM_DOCKER=(sudo -n docker --host unix:///var/run/docker.sock)
fi

system_docker() {
  "${SYSTEM_DOCKER[@]}" "$@"
}

rootless_available() {
  docker --context rootless info >/dev/null 2>&1
}

cleanup_rootless_duplicates() {
  if ! rootless_available; then
    return 0
  fi

  mapfile -t duplicate_ids < <(
    docker --context rootless ps -aq \
      --filter "label=com.docker.compose.project=${PROJECT_NAME}"
  )
  if [ "${#duplicate_ids[@]}" -eq 0 ]; then
    return 0
  fi

  echo "Removing duplicate rootless automyai containers: ${duplicate_ids[*]}"
  docker --context rootless rm -f "${duplicate_ids[@]}" >/dev/null
}

compose() {
  local env_args=(--env-file "$PORTS_FILE")
  if [ -f "$SECRETS_FILE" ]; then
    env_args+=(--env-file "$SECRETS_FILE")
  fi
  system_docker compose "${env_args[@]}" --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" "$@"
}

doctor() {
  echo "Canonical daemon: system Docker (/var/run/docker.sock)"
  echo
  echo "System Docker project:"
  compose ps -a || true
  echo
  echo "Rootless duplicates:"
  if rootless_available; then
    docker --context rootless ps -a \
      --filter "label=com.docker.compose.project=${PROJECT_NAME}" \
      --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}'
  else
    echo "rootless Docker context unavailable"
  fi
  echo
  echo "Relevant host ports:"
  # shellcheck disable=SC1090
  source "$PORTS_FILE"
  local port_pattern
  port_pattern="${AUTOMYAI_PORT}|${VNC_PORT}|${NOVNC_PORT}|${FLARESOLVERR_PORT}|${OPENAI2_PORT}|${OPENAI3_PORT}|${OPENAI3_MAIL_PORT}|${OPENAI4_PORT}|${OPENAI5_PORT}|${EXTRACT_API_PORT}|${PAYPAL_PROTOCOL_PORT}|${CARD_PAYMENT_PORT}|${FINGERPRINT_API_PORT}|${GROK2_PORT}"
  ss -ltnp 2>/dev/null | grep -E ":(${port_pattern})\\b" || true
  echo
  local container
  for container in automyai automyai-desktop automyai-extract-api automyai-fingerprint-api automyai-paypal-protocol automyai-card-payment-portal automyai-flaresolverr; do
    system_docker inspect -f \
      '{{.Name}} restart_count={{.RestartCount}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}' \
      "$container" 2>/dev/null || true
  done
}

require_services() {
  if [ "$#" -eq 0 ]; then
    echo "Specify at least one service: ${CORE_SERVICES[*]}" >&2
    exit 2
  fi
}

command_name="${1:-doctor}"
if [ "$#" -gt 0 ]; then
  shift
fi

case "$command_name" in
  up)
    cleanup_rootless_duplicates
    if [ "$#" -eq 0 ]; then
      # Safe boot: start missing modules but never recreate healthy running
      # modules merely because another service's config changed.
      compose up -d --no-build --no-recreate --remove-orphans
    else
      compose up -d --no-build --no-deps "$@"
    fi
    ;;
  deploy)
    cleanup_rootless_duplicates
    require_services "$@"
    compose up -d --build --no-deps "$@"
    ;;
  build)
    require_services "$@"
    compose build "$@"
    ;;
  deploy-all)
    cleanup_rootless_duplicates
    compose up -d --build --remove-orphans
    ;;
  down)
    echo "'down' is intentionally disabled because it stops every Automyai module." >&2
    echo "Use '$0 stop SERVICE...' or '$0 down-all' explicitly." >&2
    exit 2
    ;;
  down-all)
    compose down "$@"
    ;;
  stop)
    require_services "$@"
    compose stop "$@"
    ;;
  restart)
    cleanup_rootless_duplicates
    require_services "$@"
    compose restart "$@"
    ;;
  ps|status)
    compose ps -a
    ;;
  logs)
    compose logs "$@"
    ;;
  exec)
    cleanup_rootless_duplicates
    compose exec "$@"
    ;;
  doctor)
    doctor
    ;;
  cleanup-duplicates)
    cleanup_rootless_duplicates
    ;;
  *)
    echo "Usage: $0 {up [SERVICE...]|build SERVICE...|deploy SERVICE...|deploy-all|stop SERVICE...|down-all|restart SERVICE...|ps|logs|exec|doctor|cleanup-duplicates}" >&2
    exit 2
    ;;
esac
