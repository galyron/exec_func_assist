#!/usr/bin/env bash
# deploy.sh — push latest code to mbox and restart the Docker stack.
#
# Usage:  ./deploy.sh

set -euo pipefail

MBOX_USER="${MBOX_USER:-gmate}"
MBOX_HOST="${MBOX_HOST:-mbox}"

echo "▶ Deploying to ${MBOX_USER}@${MBOX_HOST}"

ssh "${MBOX_USER}@${MBOX_HOST}" bash <<'EOF'
  set -euo pipefail
  cd ~/services/exec_func_assist
  echo "  pulling latest code..."
  git pull --ff-only
  echo "  stopping old bot container before rebuild..."
  docker compose -f docker-compose.yml -f docker-compose.prod.yml stop bot || true
  echo "  rebuilding and restarting containers..."
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

  # ── Deploy log entry ────────────────────────────────────────────────────────
  DEPLOY_LOG="deploy.log"
  TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S %Z")
  BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
  COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
  COMMIT_MSG=$(git log -1 --pretty=format:"%s" 2>/dev/null || echo "unknown")
  {
    printf "\n\n"
    printf "════════════════════════════════════════════════════════\n"
    printf "  DEPLOY  %s\n" "${TIMESTAMP}"
    printf "  Branch: %s  |  Commit: %s\n" "${BRANCH}" "${COMMIT}"
    printf "  %s\n" "${COMMIT_MSG}"
    printf "════════════════════════════════════════════════════════\n"
  } >> "${DEPLOY_LOG}"
  echo "  logged to ${DEPLOY_LOG}."

  echo "  done."
EOF

echo "✓ Deploy complete."
