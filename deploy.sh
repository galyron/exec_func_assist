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
  echo "  rebuilding and restarting containers..."
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
  echo "  done."
EOF

echo "✓ Deploy complete."
