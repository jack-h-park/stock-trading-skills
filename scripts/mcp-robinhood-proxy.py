#!/usr/bin/env python3
"""
mcp-robinhood-proxy.py — stdio<->HTTP proxy for the Robinhood agent MCP.

Hermes connects to this script via the stdio MCP transport. This script
relays JSON-RPC to the Robinhood HTTP MCP endpoint with a fresh Bearer
token, using the same token file that the daily cron uses.

Configured as the command of the trader Hermes profile's Robinhood MCP
servers (via hermes-control-plane's configure-live-trader.sh), and spawned by
the scheduled review. Replaces the broken auth:oauth approach.

Three modes, chosen per registration by flag. Each server entry that points at
this script picks exactly one, so the rule travels with the server rather than
with whichever CLI or agent happens to call it.

DEFAULT (no flag): relays every tool unfiltered. The trader gateway's single
`robinhood` server used this mode until its config registers the two servers
below (a hermes-control-plane change); no caller should need it after that.

READ-ONLY MODE (--read-only): refuses every tool that is not a read, and hides
those tools from tools/list so a caller never sees one to call. Used by:

  * The scheduled review (scripts/run-review.sh), which must never trade. Its
    `codex exec` call also passes `enabled_tools` (scripts/codex_args.py, since
    2026-09-28; before that it was `claude -p --allowedTools`), which scopes the
    job to the reads it needs. That list is per-job; this proxy is the floor
    nothing can go under, and it does not depend on which CLI runs the job.
  * The trader profile's `robinhood` server: all research, tax and
    reconciliation work.

AGENTIC-ORDERS MODE (--agentic-orders): the trader profile's `robinhood-orders`
server, which carries the confirm-before-place order protocol. It relays only
the six tools in AGENTIC_ORDER_TOOLS and hides the rest from tools/list. Option,
crypto and exercise orders, watchlist and alert edits, scans and trade-approval
declines are all refused. On top of that, review_equity_order,
place_equity_order and cancel_equity_order are refused unless
`arguments.account_number` equals the Agentic account number, which is read from
the gitignored config/accounts.local.md (scripts/accounts_local.py, the same
parser tax_guard.py uses) on every call. A missing file, a missing Agentic row
or a missing argument refuses the call: this check fails closed.

cancel_equity_order takes `account_number` as a required argument alongside
`order_id`, and Robinhood rejects a cancel whose account does not own the order.
So cancel gets the same argument check as review/place, with no order lookup: a
cancel naming the Agentic account cannot reach an order in another account,
because upstream refuses the mismatch.

Robinhood's own `agentic_allowed` flag remains the outer gate on every order;
this check is the inner one, and Hermes `tools.include` is a third layer that
decides what the model is shown. Refusal messages name the rule that refused
and never echo an account number.

--read-only and --agentic-orders are mutually exclusive; passing both exits 2
before anything is relayed.

The read-only rule is an ALLOW-list, not a deny-list, because a deny-list fails
open: a write tool added to the Robinhood MCP later would pass one. Robinhood
names every read `get_*` (plus `search`), so allowing those two shapes and
refusing the rest means a new tool is refused until someone adds it here in a
commit. The cost is that a read tool named some other way is refused too; that
breaks a report, which is the safe direction to fail. --agentic-orders is an
allow-list of exact names for the same reason.

Token file: ~/.hermes/profiles/trader/mcp-tokens/robinhood.json

Runs under the ops host's system /usr/bin/python3 (same as robinhood_token.py,
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
from accounts_local import account_for  # noqa: E402
from robinhood_token import get_access_token  # noqa: E402

MCP_URL = "https://agent.robinhood.com/mcp/trading"

# Tool-name shapes that are reads. In --read-only mode anything else is refused
# --- see the ALLOW-list rationale in the module docstring.
READ_TOOL_PREFIXES = ("get_",)
READ_TOOL_NAMES = frozenset({"search"})


# --agentic-orders: the only tools relayed, and the subset whose account is checked.
AGENTIC_ORDER_TOOLS = frozenset({
    "get_accounts",
    "get_portfolio",
    "get_equity_orders",
    "review_equity_order",
    "place_equity_order",
    "cancel_equity_order",
})
ACCOUNT_CHECKED_TOOLS = frozenset({
    "review_equity_order",
    "place_equity_order",
    "cancel_equity_order",
})

# Where the Agentic number is read from. A module attribute so tests can point
# it at a synthetic file; None means scripts/accounts_local.py's default path.
ACCOUNTS_LOCAL: Any = None

READ_ONLY = False
AGENTIC_ORDERS = False


def is_read_tool(name: str) -> bool:
    """True for a Robinhood tool that only reads."""
    return name in READ_TOOL_NAMES or name.startswith(READ_TOOL_PREFIXES)


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _refusal(msg_id: Any, name: str) -> dict[str, Any]:
    """A JSON-RPC error standing in for a tool call that was never sent.

    -32601 (method not found) rather than a generic failure: to the caller the
    tool genuinely does not exist here, which is what tools/list also reports.
    """
    return _error(
        msg_id, -32601,
        f"tool {name!r} is not available: this proxy is read-only and "
        "relays only Robinhood read tools",
    )


def _agentic_tool_refusal(msg_id: Any, name: str) -> dict[str, Any]:
    return _error(
        msg_id, -32601,
        f"tool {name!r} is not available: this proxy is in agentic-orders mode and "
        "relays only " + ", ".join(sorted(AGENTIC_ORDER_TOOLS)),
    )


def _account_refusal(msg_id: Any, name: str, reason: str) -> dict[str, Any]:
    """-32602 (invalid params): the tool exists, these arguments may not use it.

    `reason` names the rule. It never carries an account number, the caller's or
    the configured one, so a refusal can be logged or shown without leaking one.
    """
    return _error(
        msg_id, -32602,
        f"{name} refused: orders may only target the Agentic account ({reason}); "
        "nothing was sent to Robinhood",
    )


def agentic_account() -> str | None:
    """The Agentic account number from config/accounts.local.md, or None."""
    return account_for("Agentic", ACCOUNTS_LOCAL)


def check_agentic_call(msg: dict[str, Any]) -> dict[str, Any] | None:
    """In --agentic-orders mode: None to relay, or the refusal to send instead."""
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    name = str(params.get("name", ""))
    msg_id = msg.get("id")
    if name not in AGENTIC_ORDER_TOOLS:
        return _agentic_tool_refusal(msg_id, name)
    if name not in ACCOUNT_CHECKED_TOOLS:
        return None
    args = params.get("arguments")
    given = args.get("account_number") if isinstance(args, dict) else None
    if not isinstance(given, str) or not given:
        return _account_refusal(msg_id, name, "account_number argument is missing")
    expected = agentic_account()
    if not expected:
        return _account_refusal(
            msg_id, name,
            "the Agentic account could not be resolved from config/accounts.local.md",
        )
    if given != expected:
        return _account_refusal(msg_id, name, "account_number is not the Agentic account")
    return None


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


def _strip_to_agentic_orders(payload: dict[str, Any]) -> dict[str, Any]:
    """--agentic-orders counterpart of _strip_write_tools: keep the six, drop the rest."""
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    tools = result.get("tools")
    if not isinstance(tools, list):
        return payload
    result["tools"] = [
        t for t in tools
        if not isinstance(t, dict) or str(t.get("name", "")) in AGENTIC_ORDER_TOOLS
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


def _refuse(payload: dict[str, Any]) -> None:
    # Refused here, so nothing is sent to Robinhood at all.
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main() -> None:
    global READ_ONLY, AGENTIC_ORDERS
    flags = sys.argv[1:]
    READ_ONLY = "--read-only" in flags
    AGENTIC_ORDERS = "--agentic-orders" in flags
    if READ_ONLY and AGENTIC_ORDERS:
        sys.stderr.write(
            "mcp-robinhood-proxy: --read-only and --agentic-orders are mutually "
            "exclusive; register one server per mode\n"
        )
        sys.exit(2)
    token = _get_token()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        method = msg.get("method")
        if method == "tools/call":
            if READ_ONLY:
                name = str((msg.get("params") or {}).get("name", ""))
                if not is_read_tool(name):
                    _refuse(_refusal(msg.get("id"), name))
                    continue
            if AGENTIC_ORDERS:
                refusal = check_agentic_call(msg)
                if refusal is not None:
                    _refuse(refusal)
                    continue

        try:
            token = _get_token()
        except Exception:
            pass
        transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None
        if method == "tools/list":
            if READ_ONLY:
                transform = _strip_write_tools
            elif AGENTIC_ORDERS:
                transform = _strip_to_agentic_orders
        _relay(msg, token, transform)


if __name__ == "__main__":
    main()
