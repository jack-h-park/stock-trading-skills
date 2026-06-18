#!/usr/bin/env python3
"""
Get or refresh the Robinhood MCP OAuth token from the Hermes trader profile's
token store.  Outputs the current valid access_token to stdout.

If the token is expired (or within 5 minutes of expiry), it refreshes using
the stored refresh_token before printing.

Usage:
    python3 _get_robinhood_token.py
Exit code:
    0 — valid token on stdout
    1 — refresh failed (error message on stderr)
"""

import json
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

TOKEN_FILE = Path.home() / ".hermes/profiles/trader/mcp-tokens/robinhood.json"
CLIENT_ID = "LtLiNmbs9owbYfWgBlC68Z2VujIPuvGoAiSYr8xW"
TOKEN_ENDPOINT = "https://api.robinhood.com/oauth2/token/"
EXPIRY_BUFFER_SECONDS = 300  # refresh 5 min before actual expiry


def load_token() -> dict:
    with open(TOKEN_FILE) as f:
        return json.load(f)


def save_token(data: dict) -> None:
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)


def refresh_token(refresh_tok: str) -> dict:
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_tok,
            "client_id": CLIENT_ID,
        }
    ).encode()
    req = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main() -> None:
    try:
        token = load_token()
    except FileNotFoundError:
        print(f"Token file not found: {TOKEN_FILE}", file=sys.stderr)
        sys.exit(1)

    expires_at = float(token.get("expires_at", 0))
    if time.time() + EXPIRY_BUFFER_SECONDS >= expires_at:
        try:
            new = refresh_token(token["refresh_token"])
        except Exception as e:
            print(f"Token refresh failed: {e}", file=sys.stderr)
            sys.exit(1)

        token["access_token"] = new["access_token"]
        if "refresh_token" in new:
            token["refresh_token"] = new["refresh_token"]
        token["expires_in"] = new.get("expires_in", 86400)
        token["expires_at"] = time.time() + float(new.get("expires_in", 86400))
        save_token(token)

    print(token["access_token"], end="")


if __name__ == "__main__":
    main()
