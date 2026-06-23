#!/usr/bin/env python3
"""
mcp-robinhood-proxy.py — stdio<->HTTP proxy for the Robinhood agent MCP.

Hermes connects to this script via the stdio MCP transport. This script
relays JSON-RPC to the Robinhood HTTP MCP endpoint with a fresh Bearer
token, using the same token file that the daily cron uses.

Configured as mcp_servers.robinhood.command in the trader Hermes profile
(via configure-live-trader.sh). Replaces the broken auth:oauth approach.

Token file: ~/.hermes/profiles/trader/mcp-tokens/robinhood.json
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
EXPIRY_BUFFER = 300  # refresh 5 min before expiry
MCP_URL = "https://agent.robinhood.com/mcp/trading"

_session_id = None


def _get_token():
    with open(TOKEN_FILE) as f:
        token = json.load(f)
    if time.time() + EXPIRY_BUFFER >= float(token.get("expires_at", 0)):
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": CLIENT_ID,
        }).encode()
        req = urllib.request.Request(
            TOKEN_ENDPOINT, data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            new = json.loads(resp.read())
        token["access_token"] = new["access_token"]
        if "refresh_token" in new:
            token["refresh_token"] = new["refresh_token"]
        token["expires_at"] = time.time() + float(new.get("expires_in", 86400))
        with open(TOKEN_FILE, "w") as f:
            json.dump(token, f, indent=2)
    return token["access_token"]


def _relay(msg, token):
    global _session_id
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token,
        "Accept": "application/json, text/event-stream",
    }
    if _session_id:
        headers["Mcp-Session-Id"] = _session_id
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(msg).encode(), headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            sid = resp.getheader("Mcp-Session-Id")
            if sid:
                _session_id = sid
            ct = resp.getheader("Content-Type", "")
            raw = resp.read().decode(errors="replace")
            if "text/event-stream" in ct:
                for line in raw.splitlines():
                    if line.startswith("data: "):
                        payload = line[6:].strip()
                        if payload and payload != "[DONE]":
                            sys.stdout.write(payload + "\n")
                            sys.stdout.flush()
            else:
                stripped = raw.strip()
                if stripped:
                    sys.stdout.write(stripped + "\n")
                    sys.stdout.flush()
    except Exception as exc:
        err = {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "error": {"code": -32603, "message": str(exc)},
        }
        sys.stdout.write(json.dumps(err) + "\n")
        sys.stdout.flush()


def main():
    token = _get_token()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            token = _get_token()
        except Exception:
            pass
        _relay(msg, token)


if __name__ == "__main__":
    main()
