#!/usr/bin/env bash
# Automyai git autosave to private GitHub (only when there are changes).
set -euo pipefail

if [[ "${AUTOMYAI_AUTOSAVE_ENABLED:-false}" != "true" ]]; then
  echo "automyai autosave is disabled; use a reviewed manual commit" >&2
  exit 0
fi

REPO_DIR="${AUTOMYAI_DIR:-/opt/automyai}"
BRANCH="${AUTOMYAI_GIT_BRANCH:-main}"
REMOTE="${AUTOMYAI_GIT_REMOTE:-origin}"
LOCK_DIR="${REPO_DIR}/.git/autosave.lock"
LOG_DIR="${REPO_DIR}/logs"
LOG_FILE="${LOG_DIR}/git_autosave.log"
mkdir -p "$LOG_DIR"

ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "[$(ts)] $*" | tee -a "$LOG_FILE"; }

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if [[ -d "$LOCK_DIR" ]]; then
    age=$(( $(date +%s) - $(stat -c %Y "$LOCK_DIR" 2>/dev/null || echo 0) ))
    if (( age > 600 )); then
      rm -rf "$LOCK_DIR"
      mkdir "$LOCK_DIR"
    else
      log "skip: another autosave is running"
      exit 0
    fi
  fi
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

cd "$REPO_DIR"
[[ -d .git ]] || { log "error: not a git repo"; exit 1; }

"$REPO_DIR/scripts/check.sh" >>"$LOG_FILE" 2>&1

git config user.name >/dev/null 2>&1 || git config user.name "automyai-autosave"
git config user.email >/dev/null 2>&1 || git config user.email "automyai-autosave@local"

# only commit if there are real changes
if [[ -z "$(git status --porcelain)" ]]; then
  log "clean: nothing to commit"
  exit 0
fi

git add -A
if git diff --cached --quiet; then
  log "clean: no staged changes"
  exit 0
fi

HOST="$(hostname -s 2>/dev/null || echo host)"
COUNT="$(git diff --cached --name-only | wc -l | tr -d ' ')"
MSG="autosave: ${COUNT} files on ${HOST} @ $(ts)"
git commit -m "$MSG" >/dev/null
log "committed: $MSG"

if [[ "${AUTOMYAI_AUTOSAVE_PUSH:-false}" == "true" ]]; then
  if git push "$REMOTE" "HEAD:${BRANCH}" >>"$LOG_FILE" 2>&1; then
    log "pushed: $(git rev-parse --short HEAD) -> ${REMOTE}/${BRANCH}"
  else
    log "error: push failed"
    exit 1
  fi
else
  log "push disabled; review the local commit manually"
fi
