#!/usr/bin/env bash
# deploy.sh — push latest code to mbox and restart the Docker stack.
#
# Usage:  ./deploy.sh
#
# Set MBOX_USER and MBOX_HOST below, or export them before running.

set -euo pipefail

MBOX_USER="${MBOX_USER:-gabriell}"        # your username on mbox
MBOX_HOST="${MBOX_HOST:-192.168.178.24}"  # mbox LAN IP
REMOTE_DIR="${REMOTE_DIR:-~/exec_func_assist}"

echo "▶ Deploying to ${MBOX_USER}@${MBOX_HOST}:${REMOTE_DIR}"

ssh "${MBOX_USER}@${MBOX_HOST}" bash <<EOF
  set -euo pipefail
  cd "${REMOTE_DIR}"
  echo "  pulling latest code..."
  git pull --ff-only
  echo "  rebuilding and restarting containers..."
  docker compose up -d --build
  echo "  done."
EOF

echo "✓ Deploy complete."
