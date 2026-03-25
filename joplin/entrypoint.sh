#!/bin/sh
# Joplin CLI container entrypoint.
#
# Required env vars (from .env via docker-compose):
#   JOPLIN_API_TOKEN     — the REST API auth token (you define this, any random string)
#   JOPLIN_DROPBOX_AUTH  — Dropbox OAuth token JSON
#
# One-time setup to obtain JOPLIN_DROPBOX_AUTH:
#   1. docker compose run --rm joplin sh
#   2. joplin config api.token "$JOPLIN_API_TOKEN"
#   3. joplin config sync.target 7
#   4. joplin sync          ← follow the browser auth prompt
#   5. joplin config sync.7.auth   ← copy this output
#   6. exit
#   7. Add to .env: JOPLIN_DROPBOX_AUTH='<output from step 5>'
#   8. docker compose up --build

set -e

# ── Validate required env vars ────────────────────────────────────────────────

if [ -z "$JOPLIN_API_TOKEN" ]; then
  echo "ERROR: JOPLIN_API_TOKEN is not set." >&2
  exit 1
fi

if [ -z "$JOPLIN_DROPBOX_AUTH" ]; then
  echo "ERROR: JOPLIN_DROPBOX_AUTH is not set." >&2
  echo "" >&2
  echo "Run the one-time setup:" >&2
  echo "  docker compose run --rm joplin sh" >&2
  echo "  joplin config sync.target 7" >&2
  echo "  joplin sync    # follow the Dropbox auth prompts" >&2
  echo "  joplin config sync.7.auth    # copy this output" >&2
  echo "  exit" >&2
  echo "  # Add to .env: JOPLIN_DROPBOX_AUTH='<copied output>'" >&2
  exit 1
fi

# ── Configure Joplin (writes to mounted volume — persists across restarts) ────

echo "[joplin] Configuring..."
joplin config api.token "$JOPLIN_API_TOKEN"
joplin config sync.target 7
joplin config sync.7.auth "$JOPLIN_DROPBOX_AUTH"
# Joplin server only binds to 127.0.0.1; we run it on 41185 internally and
# use socat to forward 0.0.0.0:41184 → 127.0.0.1:41185 so other containers
# can reach it via Docker DNS.
joplin config api.port 41185

# ── Initial sync ──────────────────────────────────────────────────────────────

echo "[joplin] Running initial sync..."
joplin sync && echo "[joplin] Initial sync complete." || echo "[joplin] Initial sync failed — server will start anyway, bot will retry."

# ── Background sync loop (every 15 min during active hours) ──────────────────
# The bot reads from the REST API; Joplin must pull from Dropbox independently.

(
  SYNC_INTERVAL=900  # 15 minutes
  while true; do
    sleep "$SYNC_INTERVAL"
    echo "[joplin] Running scheduled sync..."
    joplin sync && echo "[joplin] Sync complete." || echo "[joplin] Sync failed — will retry next cycle."
  done
) &

# ── Start REST API server + socat forwarder ───────────────────────────────────
# Joplin binds to 127.0.0.1:41185; socat exposes it on 0.0.0.0:41184.

echo "[joplin] Starting REST API server on internal port 41185..."
joplin server start &

# Wait for Joplin to be ready (up to 30 s)
for i in $(seq 1 30); do
  if wget -qO- http://127.0.0.1:41185/ping >/dev/null 2>&1; then
    echo "[joplin] API ready."
    break
  fi
  sleep 1
done

echo "[joplin] Starting socat forwarder on 0.0.0.0:41184 → 127.0.0.1:41185..."
exec socat TCP-LISTEN:41184,bind=0.0.0.0,fork,reuseaddr TCP:127.0.0.1:41185
