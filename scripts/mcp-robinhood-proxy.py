#!/usr/bin/env python3
"""
mcp-robinhood-proxy.py — stdio<->HTTP proxy for the Robinhood agent MCP.

Hermes connects to this script via the stdio MCP transport. This script
relays JSON-RPC to the Robinhood HTTP MCP endpoint with a fresh Bearer
token, using the same token file that the daily cron uses.

Configured as mcp_servers.robinhood.command in the trader Hermes profile
(via configure-live-trader.sh). Replaces the broken auth:oauth approach.

READ-ONLY MODE (--read-only): refuses every tool that is not a read, and hides
those tools from tools/list so a caller never sees one to call. OFF by default,
because the two callers need opposite things:

  * The trader Hermes agent needs writes. config/guardrails.md grants it
    place_equity_order --- confirm-before-place is how Jack executes a proposal,
    and a narrow standing authorization lets it auto-place buys under $100. So
    the gateway keeps using this proxy unfiltered.
  * The scheduled review must never trade. Today that is guaranteed by the
    `--allowedTools` whitelist on the `claude -p` call in scripts/run-review.sh
    --- a flag belonging to one CLI. `codex exec` has no equivalent, so swapping
    which CLI runs the review would drop the guarantee on the floor.

So the review gets its own read-only instance of this proxy instead of talking
to Robinhood over plain HTTP, and the rule moves from a CLI flag to the server
the job is pointed at. Which CLI and model run the review then becomes free to
change without touching the guard.

The rule is an ALLOW-list, not a deny-list, because a deny-list fails open: a
write tool added to the Robinhood MCP later would pass one. Robinhood names
every read `get_*` (plus `search`), so allowing those two shapes and refusing
the rest means a new tool is refused until someone adds it here in a commit.
The cost is that a read tool named some other way is refused too; that breaks a
report, which is the safe direction to fail.

run-review.sh keeps its own, tighter `--allowedTools` list. That one scopes a
job to the 7 tools it needs; this one is the floor nothing can go under.

Token file: ~/.hermes/profiles/trader/mcp-tokens/robinhood.json

Runs under the iMac's system /usr/bin/python3 (same as robinhood_token.py,
which it imports), not the Hermes venv. `from __future__ import annotations`
makes every annotation a lazy string, so PEP 604 `X | Y` syntax below never
actually executes on 3.9.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections.abc import Callable
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from robinhood_token import get_access_token  # noqa: E402

MCP_URL = "https://agent.robinhood.com/mcp/trading"

# Tool-name shapes that are reads. In --read-only mode anything else is refused
# --- see the ALLOW-list rationale in the module docstring.
READ_TOOL_PREFIXES = ("get_",)
READ_TOOL_NAMES = frozenset({"search"})


READ_ONLY = False


def is_read_tool(name: str) -> bool:
    """True for a Robinhood tool that only reads."""
    return name in READ_TOOL_NAMES or name.startswith(READ_TOOL_PREFIXES)


def _refusal(msg_id: Any, name: str) -> dict[str, Any]:
    """A JSON-RPC error standing in for a tool call that was never sent.

    -32601 (method not found) rather than a generic failure: to the caller the
    tool genuinely does not exist here, which is what tools/list also reports.
    """
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {
            "code": -32601,
            "message": (
                f"tool {name!r} is not available: this proxy is read-only and "
                "relays only Robinhood read tools"
            ),
        },
    }


def _strip_write_tools(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop non-read tools from a tools/list result, in place of the caller.

    Refusing the call is what makes an order impossible; removing the tool from
    the listing is what keeps a model from trying. A caller that never sees
    place_equity_order does not spend a turn being told no.
    """
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    tools = result.get("tools")
    if not isinstance(tools, list):
        return payload
    result["tools"] = [
        t for t in tools
        if not isinstance(t, dict) or is_read_tool(str(t.get("name", "")))
    ]
    return payload


_session_id: str | None = None


def _get_token() -> str:
    """Delegate to the shared store — single-flight refresh + atomic write.

    This process is respawned on every MCP reconnect and killed on every
    gateway restart, so it is the likelier of the two writers to race or to be
    interrupted mid-write. See robinhood_token for what that used to cost.
    """
    return get_access_token()


def _emit(raw: str, transform: Callable[[dict[str, Any]], dict[str, Any]] | None) -> None:
    """Write one JSON-RPC payload to stdout, transformed if asked.

    Unparseable text is passed through untouched: this proxy is a relay, and a
    payload it cannot read is not a payload it should rewrite.
    """
    if transform is not None:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            raw = json.dumps(transform(parsed))
    sys.stdout.write(raw + "\n")
    sys.stdout.flush()


def _relay(
    msg: dict[str, Any],
    token: str,
    transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> None:
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
                            _emit(payload, transform)
            else:
                stripped = raw.strip()
                if stripped:
                    _emit(stripped, transform)
    except Exception as exc:
        err = {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "error": {"code": -32603, "message": str(exc)},
        }
        sys.stdout.write(json.dumps(err) + "\n")
        sys.stdout.flush()


def main() -> None:
    global READ_ONLY
    READ_ONLY = "--read-only" in sys.argv[1:]
    token = _get_token()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if READ_ONLY and msg.get("method") == "tools/call":
            name = str((msg.get("params") or {}).get("name", ""))
            if not is_read_tool(name):
                # Refused here, so nothing is sent to Robinhood at all.
                sys.stdout.write(json.dumps(_refusal(msg.get("id"), name)) + "\n")
                sys.stdout.flush()
                continue

        try:
            token = _get_token()
        except Exception:
            pass
        transform = (
            _strip_write_tools
            if READ_ONLY and msg.get("method") == "tools/list"
            else None
        )
        _relay(msg, token, transform)


if __name__ == "__main__":
    main()
