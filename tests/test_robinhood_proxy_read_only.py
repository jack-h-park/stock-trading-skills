"""Read-only enforcement in scripts/mcp-robinhood-proxy.py.

These never reach Robinhood: `_relay` is stubbed, and the point of most of them
is that it is never called at all.

What they pin is the guarantee that used to live in one CLI flag --- and, just
as much, that it stays OFF for the caller that must be able to trade. The
scheduled review may never place an order; the trader Hermes agent must, because
config/guardrails.md grants it confirm-before-place and a narrow auto-place for
buys under $100. Both callers use this one script, so the flag is the whole
design: `test_default_mode_relays_write_tools` fails if read-only ever becomes
the default and silently disarms the interactive agent.

In read-only mode the tests check a write is refused, that nothing is relayed
when it is, that the listing hides what it refuses, and that a tool nobody has
heard of yet is refused too --- an allow-list, not a deny-list.

`test_run_review_read_tools_all_pass` is the one that binds the two lists: the
review's own whitelist is narrower, but every Robinhood tool in it has to be one
this proxy will pass, or the job breaks at the floor.

Run: python3 -m pytest tests/test_robinhood_proxy_read_only.py
"""

import importlib.util
import io
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "mcp_robinhood_proxy", REPO / "scripts" / "mcp-robinhood-proxy.py"
)
assert _spec and _spec.loader
proxy = importlib.util.module_from_spec(_spec)
sys.modules["mcp_robinhood_proxy"] = proxy
_spec.loader.exec_module(proxy)


WRITE_TOOLS = [
    "place_equity_order",
    "place_option_order",
    "place_crypto_order",
    "cancel_equity_order",
    "cancel_option_order",
    "cancel_crypto_order",
    "cancel_option_exercise",
    "exercise_option",
    "create_alert",
    "update_alert",
    "delete_alert",
    "create_watchlist",
    "update_watchlist",
    "add_to_watchlist",
    "remove_from_watchlist",
    "follow_watchlist",
    "unfollow_watchlist",
]

READ_TOOLS = [
    "get_accounts",
    "get_portfolio",
    "get_equity_positions",
    "get_equity_quotes",
    "get_equity_historicals",
    "get_equity_orders",
    "get_equity_tradability",
    "search",
]


def run_main(monkeypatch, lines, read_only=True):
    """Drive main() over stdin lines. Returns (stdout payloads, relayed msgs)."""
    relayed = []

    def fake_relay(msg, token, transform=None):
        relayed.append(msg)

    argv = ["mcp-robinhood-proxy.py"] + (["--read-only"] if read_only else [])
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(proxy, "_get_token", lambda: "tok")
    monkeypatch.setattr(proxy, "_relay", fake_relay)
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(x + "\n" for x in lines)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    proxy.main()
    payloads = [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]
    return payloads, relayed


def call(name, msg_id=1):
    return json.dumps(
        {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
         "params": {"name": name, "arguments": {}}}
    )


# ── the classifier ───────────────────────────────────────────────────────────

def test_read_tools_are_allowed():
    for name in READ_TOOLS:
        assert proxy.is_read_tool(name), name


def test_write_tools_are_refused():
    for name in WRITE_TOOLS:
        assert not proxy.is_read_tool(name), name


def test_unknown_future_tool_is_refused():
    """The allow-list half: a name nobody has written down is refused."""
    for name in ["place_futures_order", "transfer_funds", "liquidate_account", ""]:
        assert not proxy.is_read_tool(name), name


def test_run_review_read_tools_all_pass():
    """Every Robinhood tool the review whitelists must clear this proxy."""
    script = (REPO / "scripts" / "run-review.sh").read_text()
    block = re.search(r"READ_ALLOWED=\(\n(.*?)\n\)", script, re.S)
    assert block, "READ_ALLOWED array not found in run-review.sh"
    names = re.findall(r'"mcp__robinhood__([a-z_]+)"', block.group(1))
    assert len(names) >= 5, f"expected the review's robinhood tools, got {names}"
    for name in names:
        assert proxy.is_read_tool(name), f"run-review.sh allows {name}, proxy refuses it"


# ── a refused call never reaches Robinhood ───────────────────────────────────

def test_write_call_is_refused_and_never_relayed(monkeypatch):
    payloads, relayed = run_main(monkeypatch, [call("place_equity_order")])
    assert relayed == [], "a write tool call was forwarded to Robinhood"
    assert len(payloads) == 1
    assert payloads[0]["error"]["code"] == -32601
    assert "place_equity_order" in payloads[0]["error"]["message"]
    assert payloads[0]["id"] == 1


def test_read_call_is_relayed(monkeypatch):
    payloads, relayed = run_main(monkeypatch, [call("get_equity_positions")])
    assert payloads == []
    assert len(relayed) == 1
    assert relayed[0]["params"]["name"] == "get_equity_positions"


def test_every_write_tool_is_refused_end_to_end(monkeypatch):
    lines = [call(n, msg_id=i) for i, n in enumerate(WRITE_TOOLS)]
    payloads, relayed = run_main(monkeypatch, lines)
    assert relayed == []
    assert len(payloads) == len(WRITE_TOOLS)
    assert all(p["error"]["code"] == -32601 for p in payloads)


def test_non_tool_call_methods_still_relay(monkeypatch):
    lines = [json.dumps({"jsonrpc": "2.0", "id": 9, "method": "initialize"})]
    _, relayed = run_main(monkeypatch, lines)
    assert len(relayed) == 1


# ── the listing hides what the proxy refuses ─────────────────────────────────

def test_tools_list_result_drops_write_tools():
    payload = {
        "jsonrpc": "2.0", "id": 2,
        "result": {"tools": [
            {"name": "get_portfolio"},
            {"name": "place_equity_order"},
            {"name": "get_equity_quotes"},
            {"name": "cancel_equity_order"},
        ]},
    }
    kept = [t["name"] for t in proxy._strip_write_tools(payload)["result"]["tools"]]
    assert kept == ["get_portfolio", "get_equity_quotes"]


def test_tools_list_transform_is_applied_for_that_method(monkeypatch):
    seen = {}

    def fake_relay(msg, token, transform=None):
        seen[msg["method"]] = transform

    monkeypatch.setattr(sys, "argv", ["proxy", "--read-only"])
    monkeypatch.setattr(proxy, "_get_token", lambda: "tok")
    monkeypatch.setattr(proxy, "_relay", fake_relay)
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "initialize"}),
    ]
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(x + "\n" for x in lines)))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    proxy.main()
    assert seen["tools/list"] is proxy._strip_write_tools
    assert seen["initialize"] is None


def test_payload_without_tools_is_untouched():
    for payload in [
        {"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text"}]}},
        {"jsonrpc": "2.0", "id": 4, "error": {"code": -1, "message": "x"}},
        {"jsonrpc": "2.0", "id": 5, "result": "not-a-dict"},
    ]:
        assert proxy._strip_write_tools(dict(payload)) == payload


def test_emit_passes_through_unparseable_text(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    proxy._emit("not json at all", proxy._strip_write_tools)
    assert out.getvalue() == "not json at all\n"


# ── the gateway keeps its writes: read-only is opt-in ────────────────────────

def test_default_mode_relays_write_tools(monkeypatch):
    """Without --read-only nothing is filtered.

    The trader agent places orders through this proxy. If read-only ever became
    the default, confirm-before-place would fail with a tool-not-found and the
    standing authorization in config/guardrails.md would silently stop working.
    """
    payloads, relayed = run_main(
        monkeypatch, [call("place_equity_order")], read_only=False
    )
    assert payloads == []
    assert len(relayed) == 1
    assert relayed[0]["params"]["name"] == "place_equity_order"


def test_default_mode_does_not_filter_the_listing(monkeypatch):
    seen = {}

    def fake_relay(msg, token, transform=None):
        seen[msg["method"]] = transform

    monkeypatch.setattr(sys, "argv", ["proxy"])
    monkeypatch.setattr(proxy, "_get_token", lambda: "tok")
    monkeypatch.setattr(proxy, "_relay", fake_relay)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"id": 1, "method": "tools/list"}) + "\n")
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    proxy.main()
    assert seen["tools/list"] is None


def test_review_points_its_mcp_config_at_the_read_only_proxy():
    """The review must reach Robinhood through this script, with the flag.

    A plain `"type": "http"` entry would bypass the proxy entirely, which is what
    it did before --- the guard then lives only in the CLI's own tool whitelist
    and does not survive a change of CLI.
    """
    script = (REPO / "scripts" / "run-review.sh").read_text()
    assert "mcp-robinhood-proxy.py" in script
    assert "--read-only" in script
    assert '"type": "http"' not in script, "review still talks straight to Robinhood"
