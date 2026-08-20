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
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from robinhood_token import get_access_token  # noqa: E402

MCP_URL = "https://agent.robinhood.com/mcp/trading"

_session_id = None


def _get_token():
    """Delegate to the shared store — single-flight refresh + atomic write.

    This process is respawned on every MCP reconnect and killed on every
    gateway restart, so it is the likelier of the two writers to race or to be
    interrupted mid-write. See robinhood_token for what that used to cost.
    """
    return get_access_token()


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
