#!/usr/bin/env python3
"""C15 — One-time Google Calendar OAuth2 setup.

Run this on your MacBook (a browser is required for the consent flow).
Copy the resulting secrets/google_token.json to mbox before the
first production deploy.

Prerequisites:
  1. Download OAuth credentials from Google Cloud Console:
       APIs & Services → Credentials → your OAuth 2.0 Client ID → Download JSON
  2. Save the file as: secrets/google_client_secret.json

Usage:
    python setup_calendar.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CLIENT_SECRET_PATH = Path("secrets/google_client_secret.json")
TOKEN_PATH = Path("secrets/google_token.json")
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def main() -> None:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("ERROR: google-auth-oauthlib not installed. Run: pip install -r requirements.txt")
        sys.exit(1)

    if not CLIENT_SECRET_PATH.exists():
        print(f"ERROR: {CLIENT_SECRET_PATH} not found.")
        print()
        print("Steps to fix:")
        print("  1. Go to https://console.cloud.google.com")
        print("  2. Select your project (eva-bot-491123)")
        print("  3. APIs & Services → Credentials")
        print("  4. Click your OAuth 2.0 Client ID → Download JSON")
        print(f"  5. Save it as: {CLIENT_SECRET_PATH}")
        sys.exit(1)

    if TOKEN_PATH.exists():
        answer = input(f"{TOKEN_PATH} already exists. Overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            sys.exit(0)

    print("Opening browser for Google OAuth2 consent...")
    print(f"Scope requested: {SCOPES[0]}")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
    creds = flow.run_local_server(port=0)

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())

    print()
    print(f"Token saved to {TOKEN_PATH}")
    print()
    print("Next steps:")
    print("  • Local dev:  docker compose exec bot python -m connectors.calendar")
    print("  • Production: copy secrets/google_token.json to mbox before deploying")


if __name__ == "__main__":
    main()
