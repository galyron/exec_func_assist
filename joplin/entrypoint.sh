#!/bin/sh
# Joplin CLI container entrypoint.
#
# Phase 1-A: starts the REST API server with the provided token.
# Phase 1-B: add Dropbox sync configuration here before starting the server.
#
# Required env vars (from docker-compose .env):
#   JOPLIN_API_TOKEN  — the Web Clipper auth token

set -e

if [ -z "$JOPLIN_API_TOKEN" ]; then
  echo "ERROR: JOPLIN_API_TOKEN is not set." >&2
  exit 1
fi

# Set the API token Joplin will require on every request
joplin config api.token "$JOPLIN_API_TOKEN"

# TODO (Phase 1-B): configure Dropbox sync
# joplin config sync.target 7
# joplin config sync.7.auth '{"access_token":"...","token_type":"bearer"}'
# joplin sync

echo "Starting Joplin REST API server on port 41184..."
exec joplin server start
